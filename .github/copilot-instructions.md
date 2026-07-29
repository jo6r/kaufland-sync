# Copilot instrukce

Dodrzuj [AGENTS.md](../AGENTS.md) jako hlavni repozitarove voditko.

Dalsi konvence pro odpovedi Copilotu v tomto repozitari:
- Preferuj strucne, akcni upravy pred velkymi prepisy.
- Udrzuj na prvnim miste provozni spolehlivost (cron-safe logovani, explicitni osetreni chyb, predvidatelne pouziti env).
- Pri upravach deployment manifestu zachovej konzistenci image names, ConfigMap keys a secret references.
- Pokud pozadovana zmena zasahuje parsovani feedu, upozorni na mozna data-quality edge cases (encoding, prazdna pole, nevalidni stock).
