# Changelog

## 1.1.5

- foloseste o sesiune dedicata OPCOM cu `verify_ssl=False`, pentru a evita variantele diferite de erori TLS din Home Assistant/aiohttp;
- pastreaza retry-ul CSV si fallback-ul catre tabelul HTML oficial pentru ziua curenta;
- expune atributul `ssl_verification_disabled` pe senzori pentru diagnostic.

## 1.1.4

- repară regresia din 1.1.3 care a eliminat fallback-ul TLS necesar pe unele instalații Home Assistant;
- încearcă întotdeauna mai întâi conexiunea HTTPS validată și relaxează verificarea numai pentru cererile publice OPCOM care eșuează la validarea certificatului;
- repară fallback-ul către tabelul HTML oficial după epuizarea încercărilor CSV;
- aliniază intervalul curent la fusul orar al pieței OPCOM (`Europe/Berlin`).
