PREFIX ?= /usr/local
DESTDIR ?=

.PHONY: run test deb install uninstall clean

run:
	./bin/compatd

test:
	python3 -m unittest discover -s tests -v

deb:
	sh packaging/build-deb.sh

install:
	install -d $(DESTDIR)$(PREFIX)/lib/compatd
	cp -r compatd $(DESTDIR)$(PREFIX)/lib/compatd/
	find $(DESTDIR)$(PREFIX)/lib/compatd -name __pycache__ -prune -exec rm -rf {} +
	install -d $(DESTDIR)$(PREFIX)/bin
	printf '#!/bin/sh\nPYTHONPATH="$(PREFIX)/lib/compatd$${PYTHONPATH:+:$$PYTHONPATH}" exec python3 -m compatd "$$@"\n' > $(DESTDIR)$(PREFIX)/bin/compatd
	chmod 755 $(DESTDIR)$(PREFIX)/bin/compatd
	install -Dm644 data/compatd.desktop $(DESTDIR)$(PREFIX)/share/applications/compatd.desktop
	install -Dm644 data/compatd.svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/compatd.svg

uninstall:
	rm -rf $(DESTDIR)$(PREFIX)/lib/compatd
	rm -f $(DESTDIR)$(PREFIX)/bin/compatd $(DESTDIR)$(PREFIX)/share/applications/compatd.desktop $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/compatd.svg

clean:
	rm -rf build dist
	find . -name __pycache__ -prune -exec rm -rf {} +
