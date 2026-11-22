# SEO-Start für morletdb.com

## Im Repository vorbereitet

- Alle öffentlichen HTML-Seiten verwenden `https://morletdb.com/` als Basis ihrer
  Canonical- und Open-Graph-URLs. Interne relative Links bleiben gültig.
- `docs/sitemap.xml` enthält die elf öffentlichen HTML-Seiten. PDFs bleiben über
  die bestehenden HTML-Seiten erreichbar; die Sitemap ist kein vollständiges Dateiverzeichnis.
- `docs/robots.txt` erlaubt Crawling und nennt die Sitemap.
- `python3 tools/site_seo.py` generiert die Crawl-Dateien. `--check` prüft zusätzlich
  eindeutige Seitentitel/Beschreibungen, Canonicals, Social-URLs und die Domain in CNAME.
  Der Check läuft in `tools/precheck.sh` und der Website-CI.
- Neue HTML-Seiten brauchen eigene Metadaten; anschließend die Sitemap regenerieren.
- Keine erfundenen Änderungsdaten, Prioritäten oder Ranking-Versprechen.

## Nach Merge und Pages-Deployment

1. Unter https://github.com/merkhet-io/morletdb/settings/pages den DNS-Status prüfen
   und **Enforce HTTPS** aktivieren, sobald das Zertifikat bereitsteht.
2. Diese URLs im Browser prüfen:
   - https://morletdb.com/
   - https://morletdb.com/docs.html
   - https://morletdb.com/robots.txt
   - https://morletdb.com/sitemap.xml
3. Die alte Homepage `https://merkhet-io.github.io/morletdb/` sowie einen alten
   Blog-Link prüfen: Weiterleitung zur passenden neuen URL, nicht nur zur Homepage.
   Ebenso prüfen, dass `www.morletdb.com` zur gewünschten Hauptdomain führt.

## Google Search Console — einmalig im eigenen Konto

1. https://search.google.com/search-console öffnen, **Property hinzufügen** wählen.
2. Den Typ **Domain** wählen und `morletdb.com` eingeben, ohne Protokoll oder Pfad.
3. Google zeigt einen individuellen TXT-Wert wie
   `google-site-verification=...`. Den vollständigen echten Wert kopieren;
   die Punkte hier sind nur ein Platzhalter und dürfen nicht eingetragen werden.
4. Bei STRATO: **Domains → Domainverwaltung → morletdb.com → DNS → TXT-Records**.
   Einen zusätzlichen TXT-Eintrag für die Hauptdomain anlegen (Host leer bzw. `@`,
   je nach Eingabemaske). Als Wert den von Google gelieferten Text einfügen.
   Vorhandene TXT-/SPF-, MX-, A- und CNAME-Einträge dafür nicht ersetzen.
5. In der Search Console **Bestätigen** wählen. Falls der Eintrag noch nicht
   gefunden wird, DNS-Verteilung abwarten und erneut prüfen. Den TXT-Eintrag behalten.
6. Unter **Sitemaps** `https://morletdb.com/sitemap.xml` einreichen.
7. Über **URL-Prüfung** die Homepage und eine wichtige Dokumentationsseite prüfen;
   bei Bedarf Indexierung beantragen. Das ist keine Garantie für eine Aufnahme.

Ein individueller Verifizierungswert liegt im Repository nicht vor. Die Property
wurde nicht angelegt, kein DNS-Eintrag geändert und keine Sitemap eingereicht.

## Danach: Inhalte nach tatsächlicher Nachfrage priorisieren

- In der Search Console Suchanfragen, Impressionen, Klicks und indexierte Seiten beobachten.
- Themenkandidaten: waveform similarity search in PostgreSQL, vibration recordings,
  read-only MCP for engineering agents, plant graphs with Apache AGE.
- Erst Suchintention und vorhandene Inhalte prüfen; ergänzende Seiten mit konkreten
  Beispielen und belegten Grenzen schreiben. Kein nachgewiesenes Suchvolumen behaupten.
- Technische Beispiele und Blogbeiträge sinnvoll untereinander und mit der Dokumentation verlinken.

## Offizielle Referenzen

- [Google: Domain-Property hinzufügen](https://support.google.com/webmasters/answer/34592)
- [Google: Inhaberschaft per DNS bestätigen](https://support.google.com/webmasters/answer/9008080)
- [Google: Sitemap erstellen und einreichen](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Google: Canonical-URLs](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [STRATO: DNS-Einträge verwalten](https://www.strato.de/faq/domains/wie-kann-ich-bei-strato-meine-dns-eintraege-verwalten/)
