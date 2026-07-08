"""
BFSB Local HTTP Server Backend

Serves HTML templates and handles search API.
Runs on localhost:8888 (or configurable port).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, unquote

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from bfsb.core.search.manager import SyncSearchManager
from bfsb.core.search.models import MergedSearchResponse


class BFSHBServer:
    """Local HTTP server for BFSB browser."""

    def __init__(
        self,
        template_dir: Path,
        host: str = "127.0.0.1",
        port: int = 8889,
    ):
        self.template_dir = template_dir
        self.host = host
        self.port = port
        self.search_manager = SyncSearchManager()

        # Jinja2 environment for template rendering
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def _create_app(self) -> web.Application:
        """Create aiohttp application with routes."""
        app = web.Application()

        # Routes
        app.router.add_get('/', self.handle_home)
        app.router.add_get('/health', self.handle_health)
        app.router.add_get('/search', self.handle_search)
        app.router.add_get('/about', self.handle_about)
        app.router.add_get('/preferences', self.handle_preferences)
        app.router.add_static('/static', path=str(self.template_dir / 'static'), name='static')

        # CORS for local development
        app.middlewares.append(self._cors_middleware)

        return app

    @staticmethod
    async def _cors_middleware(app: web.Application, handler):
        async def middleware(request: Request) -> Response:
            response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        return middleware

    async def handle_home(self, request: Request) -> Response:
        """Serve home page - rendered through Jinja2 with PAGE_TYPE='home'."""
        template = self.jinja_env.get_template('bfsb_combined.html')
        html = template.render(PAGE_TYPE='home')
        return web.Response(text=html, content_type='text/html')

    async def handle_search(self, request: Request) -> Response:
        """Handle search query and render results within combined chrome."""
        query = request.query.get('q', '').strip()

        if not query:
            # Empty query - render combined with empty results
            template = self.jinja_env.get_template('bfsb_combined.html')
            html = template.render(
                PAGE_TYPE='results',
                QUERY='',
                RESULTS_COUNT='No results found',
                RESULTS_HTML='<div style="text-align:center;color:#a0a0b0;padding:48px;"><div style="font-size:48px;margin-bottom:16px;">🔍</div><div style="font-size:18px;margin-bottom:8px;">No results for ""</div><div style="font-size:13px;color:#55556a;">Try different keywords or check your spelling</div></div>',
                INFOBOX_HTML='<div class="infobox"><div class="infobox-title">Info</div><div style="color:#55556a;">No additional info available</div></div>',
            )
            return web.Response(text=html, content_type='text/html')

        # Perform search
        try:
            response = self.search_manager.search(query, page=1)
        except Exception as e:
            return web.Response(text=f'Search error: {e}', status=500, content_type='text/plain')

        # Format results for template
        results_html = self._format_results_html(response.results)
        count_text = f"About {response.total_results} results found"
        if response.engines_used:
            count_text += f" · Sources: {', '.join(e.value for e in response.engines_used)}"

        # Extract infobox data
        infobox_html = self._format_infobox_html(response.results)

        # Render combined template with results
        template = self.jinja_env.get_template('bfsb_combined.html')
        html = template.render(
            PAGE_TYPE='results',
            QUERY=query,
            RESULTS_COUNT=count_text,
            RESULTS_HTML=results_html,
            INFOBOX_HTML=infobox_html,
        )
        return web.Response(text=html, content_type='text/html')

    def _format_results_html(self, results) -> str:
        """Format search results as HTML."""
        if not results:
            return '<div style="text-align:center;color:#a0a0b0;padding:48px;"><div style="font-size:48px;margin-bottom:16px;">🔍</div><div style="font-size:18px;margin-bottom:8px;">No results</div></div>'

        parts = []
        for r in results:
            domain = r.url
            try:
                from urllib.parse import urlparse
                domain = urlparse(r.url).netloc.replace('www.', '')
            except Exception:
                pass

            sources_html = ''.join(
                f'<span class="source-tag">{s}</span>' for s in getattr(r, 'engines', [])
            )

            parts.append(f'''
                <div class="result-item">
                    <div class="result-url">
                        <span class="result-favicon"></span>
                        {domain}
                    </div>
                    <a class="result-title" href="{r.url}">{r.title}</a>
                    <div class="result-desc">{r.snippet}</div>
                    <div class="result-sources">{sources_html}</div>
                </div>
            ''')
        return ''.join(parts)

    def _format_infobox_html(self, results) -> str:
        """Format infobox HTML from first result."""
        if not results:
            return '<div class="infobox"><div class="infobox-title">Info</div><div style="color:#55556a;">No additional info available</div></div>'

        first = results[0]
        domain = ''
        try:
            from urllib.parse import urlparse
            domain = urlparse(first.url).netloc.replace('www.', '').lower()
        except Exception:
            pass

        # Known entities
        entity_data = {
            'youtube.com': {
                'title': 'YouTube',
                'rows': [
                    ('Founded', 'February 14, 2005'),
                    ('Country', 'United States'),
                    ('HQ', 'San Bruno, CA'),
                    ('Founders', 'Steve Chen, Chad Hurley, Jawed Karim'),
                ],
                'links': [
                    ('Wikipedia', 'https://en.wikipedia.org/wiki/YouTube'),
                    ('YouTube', 'https://www.youtube.com'),
                    ('Twitter', 'https://twitter.com/YouTube'),
                ],
            },
            'github.com': {
                'title': 'GitHub',
                'rows': [
                    ('Founded', 'April 10, 2008'),
                    ('Country', 'United States'),
                    ('HQ', 'San Francisco, CA'),
                    ('CEO', 'Thomas Dohmke'),
                ],
                'links': [
                    ('Wikipedia', 'https://en.wikipedia.org/wiki/GitHub'),
                    ('GitHub', 'https://github.com'),
                    ('Twitter', 'https://twitter.com/github'),
                ],
            },
            'wikipedia.org': {
                'title': 'Wikipedia',
                'rows': [
                    ('Founded', 'January 15, 2001'),
                    ('Country', 'United States'),
                    ('HQ', 'San Francisco, CA'),
                    ('Founder', 'Jimmy Wales, Larry Sanger'),
                ],
                'links': [
                    ('Wikipedia', 'https://en.wikipedia.org/wiki/Wikipedia'),
                    ('Wikimedia', 'https://wikimedia.org'),
                ],
            },
        }

        entity = entity_data.get(domain)
        if not entity:
            return '<div class="infobox"><div class="infobox-title">Info</div><div style="color:#55556a;">No additional info available</div></div>'

        rows_html = ''.join(
            f'<div class="infobox-row"><span class="infobox-key">{k}</span><span class="infobox-val">{v}</span></div>'
            for k, v in entity['rows']
        )
        links_html = ''.join(
            f'<a href="{url}">{label}</a>' for label, url in entity.get('links', [])
        )

        return f'''
            <div class="infobox">
                <div class="infobox-title">{entity['title']}</div>
                {rows_html}
                <div class="infobox-links">{links_html}</div>
            </div>
        '''

    async def handle_about(self, request: Request) -> Response:
        """Serve about page."""
        return web.Response(text='<html><body>BFSB Browser - About</body></html>', content_type='text/html')

    async def handle_preferences(self, request: Request) -> Response:
        """Serve preferences page."""
        return web.Response(text='<html><body>BFSB Browser - Preferences</body></html>', content_type='text/html')

    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint for server readiness."""
        return web.Response(text='OK', content_type='text/plain', status=200)

    async def start(self) -> None:
        """Start the server."""
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        print(f"[BFSHBServer] Started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        print("[BFSHBServer] Stopped")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def home_url(self) -> str:
        return f"{self.base_url}/"

    @property
    def search_url(self) -> str:
        return f"{self.base_url}/search"


# Global server instance
_server_instance: Optional[BFSHBServer] = None


async def get_server() -> BFSHBServer:
    """Get or create the global server instance."""
    global _server_instance
    if _server_instance is None:
        template_dir = Path(__file__).parent.parent / "templates"
        _server_instance = BFSHBServer(template_dir)
        await _server_instance.start()
    return _server_instance


async def shutdown_server() -> None:
    """Shutdown the global server."""
    global _server_instance
    if _server_instance:
        await _server_instance.stop()
        _server_instance = None