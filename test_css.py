import sys
from PyQt6.QtWidgets import QApplication
from quillon.core.config import PATHS
PATHS.ensure_dirs()
from quillon.core.blocker import URLBlocker

blocker = URLBlocker()
script = blocker.get_injected_script('https://www.youtube.com/watch?v=test')

# Find the CSS template literal - it starts with ` and ends with `
# The CSS is assigned to const css = `
start_marker = 'const css = `'
start = script.index(start_marker)
# Find the closing backtick - but be careful, there might be backticks inside the CSS
# The pattern is: const css = `...`; - so look for `;
end = script.index('`;', start)
css = script[start+len(start_marker):end]

print('CSS length:', len(css))
print('Contains ad-showing:', 'ad-showing' in css)
print('Contains ytp-ad-showing:', 'ytp-ad-showing' in css)
print('Contains html5-video-player:', 'html5-video-player' in css)
print()
print('Full CSS:')
print(css)