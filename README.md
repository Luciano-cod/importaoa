# ImportAOA — Guia de Instalação (3 Opções)

## OPÇÃO 1 — Rede Local (PC + Telemóvel no mesmo Wi-Fi)

Descobre o IP do teu PC (Windows: `ipconfig`, Linux/Mac: `ifconfig`),
inicia o servidor com `python app.py`, e abre `http://SEU_IP:5000` no browser
do telemóvel. Funciona enquanto o PC estiver ligado na mesma rede.

## OPÇÃO 2 — Cloud Railway/Render (acesso em qualquer lugar)

Railway: `railway login && railway init && railway up`
Render: liga o repositório GitHub e define Start Command como `python app.py`.
Tens um URL HTTPS público em menos de 5 minutos.

## OPÇÃO 3 — PWA (instalar como app no telemóvel)

Android (Chrome): banner automático ou botão dourado "Instalar App".
iPhone (Safari): botão Partilhar → "Adicionar ao Ecrã de Início".
Funciona offline para leitura; escrita requer ligação ao servidor.

## Para HTTPS local (PWA completa sem cloud)

```bash
pip install pyopenssl
# No app.py, linha final: ssl_context='adhoc'
# Acede via https://SEU_IP:5000
```
