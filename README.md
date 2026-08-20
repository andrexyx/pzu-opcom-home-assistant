<p align="center"><img src="custom_components/pzu_opcom/brand/icon.png" alt="PZU OPCOM" width="180"></p>

# PZU OPCOM for Home Assistant

Integrare locală care citește exportul CSV oficial OPCOM pentru PZU și expune prețurile în `RON/kWh`. Poate fi instalată prin HACS ca repository personalizat sau manual, fără alte integrări externe.

## Instalare

### HACS (custom repository)

1. HACS → Integrations → Custom repositories.
2. Adaugă `https://github.com/andrexyx/pzu-opcom-home-assistant` cu tipul `Integration`.
3. Instalează **PZU OPCOM**.
4. Mergi la **Settings → Devices & services → Add Integration** și caută
  **PZU OPCOM**.
5. Confirmă configurarea; nu sunt necesare credențiale.

### Manual

1. Copiază `custom_components/pzu_opcom` în `/config/custom_components/`.
2. Mergi la **Settings → Devices & services → Add Integration** și caută
  **PZU OPCOM**.
3. Confirmă configurarea; nu sunt necesare credențiale.

Configurarea prin interfața Home Assistant este metoda recomandată. Configurațiile
YAML existente cu `pzu_opcom:` rămân compatibile.

## Entități

- `sensor.pzu_pret_curent`
- `sensor.pzu_pret_ora_urmatoare`
- `sensor.pzu_pret_minim_azi`
- `sensor.pzu_pret_maxim_azi`
- `sensor.pzu_pret_mediu_azi`
- `sensor.pzu_strategie_baterie`

Senzorul de strategie oferă atributele `prag_incarcare` și `prag_vanzare`. Erorile de rețea nu sunt convertite în prețul `0`; ultima citire validă este păstrată, iar fără date valide entitățile devin indisponibile.

Datele sunt actualizate la fiecare 30 de minute în fusul orar
`Europe/Bucharest`. Exportul OPCOM este în lei/MWh și este convertit automat în
RON/kWh prin împărțire la 1000.

Sursa oficială: [OPCOM – Rezultate PZU RO](https://www.opcom.ro/grafice-ip-raportPIP-si-volumTranzactionat/ro).

## Card Lovelace compatibil

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.pzu_strategie_baterie
    name: Strategie Baterie
    icon: mdi:battery-sync
    color: blue
    state_content:
      - state
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: sensor.pzu_pret_curent
        name: Preț Curent
        icon: mdi:currency-eur
        color: amber
      - type: tile
        entity: sensor.pzu_pret_ora_urmatoare
        name: Ora Următoare
        icon: mdi:clock-outline
        color: orange
  - type: entities
    title: Statistici & Praguri PZU
    show_header_toggle: false
    entities:
      - entity: sensor.pzu_pret_minim_azi
        name: Preț Minim Azi
        icon: mdi:arrow-down-bold-circle-outline
      - entity: sensor.pzu_pret_maxim_azi
        name: Preț Maxim Azi
        icon: mdi:arrow-up-bold-circle-outline
      - entity: sensor.pzu_pret_mediu_azi
        name: Preț Mediu Azi
        icon: mdi:calculator
      - type: attribute
        entity: sensor.pzu_strategie_baterie
        attribute: prag_incarcare
        name: Prag Încărcare (<=)
        icon: mdi:battery-charging-100
      - type: attribute
        entity: sensor.pzu_strategie_baterie
        attribute: prag_vanzare
        name: Prag Vânzare / Descărcare (>=)
        icon: mdi:battery-arrow-down
```
