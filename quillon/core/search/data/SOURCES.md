# Blocklist data provenance

Generated files in this directory. Regenerate with the commands below; do not
hand-edit.

## `adult_domains.txt` — 89,745 adult domains

| Source | Entries | URL |
| --- | --- | --- |
| StevenBlack/hosts, `porn-only` alternate | 76,788 | `https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts` |
| Dan Pollock's "zero" hosts list | 13,090 | `https://someonewhocares.org/hosts/zero/hosts` |

Both are long-standing, publicly maintained blocklists. StevenBlack's `porn-only`
alternate is the adult subset of a list aggregated from several reputable
sources; the Dan Pollock list is a general abuse list, so it contributes some
non-adult entries — it is included for reach, and every entry is still
domain-exact, so nothing is blocked by name-matching alone.

Retrieved 25 September 2026. Entries are the host column of each hosts file,
lower-cased, comments and loopback addresses removed.

## `adult_labels.txt` — 53,925 host labels

Derived from `adult_domains.txt` so that a site named in a *search query*
("best xhamster clips") is caught without scanning 89k domains per keystroke.
The label set is what makes single-token O(1) lookups possible.

Derivation, in order:

1. take every label of every domain, not just the registrable one, so sites
   hosted on a shared suffix (`kurozenzen.github.io`) are still captured by
   their own name;
2. drop labels of length < 4;
3. drop labels appearing on more than 400 domains, which are shared CDN and
   infrastructure tokens;
4. drop labels in a hand-maintained denylist of hosting suffixes, web brands and
   ordinary qualifiers (`cdn`, `static`, `github`, `youtube`, `reddit`,
   `facebook`, `free`, `model`, `mobile`, `premium`, `blog`, …);
5. drop any label that is a real English word, using the 370,105-word
   `dwyl/english-words` list — a word like "model" or "mobile" is far too
   ambiguous to block on its own.

That last filter is what keeps normal searching intact: it removed 633 labels
that would otherwise have blocked ordinary queries.

## Regenerating

```sh
curl -sS -o sb_porn_hosts.txt \
  https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts
curl -sS -o dp_hosts.txt https://someonewhocares.org/hosts/zero/hosts
curl -sS -o words_alpha.txt \
  https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt
# then run the parse/derive steps documented in the commit that added these files
```
