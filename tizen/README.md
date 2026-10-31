# Tela inicial TV Tuner (app Tizen)

Relógio, favoritos, entradas HDMI e todos os apps — sem anúncio, sem rede.
`Amarelo (C)` fixa um app nos favoritos.

**Limite:** a tecla Home física continua abrindo a Home da Samsung. Para a TV
ligar direto aqui: abra o app uma vez e ative *Executar último app
automaticamente* (`./tv checklist`, item 6).

## Pré-requisitos (uma vez)

1. **Modo Desenvolvedor na TV:** Apps → digite `1 2 3 4 5` no controle →
   Developer mode **On** → Host PC IP = IP do computador que vai instalar →
   reinicie a TV (segure Power até reiniciar). Confirme: `./tv info` mostra a
   porta 26101 aberta.
2. **Tizen Studio CLI** (só para assinar), instalado em `~/tizen-studio`:
   ```sh
   curl -LO https://download.tizen.org/sdk/Installer/tizen-studio_6.1/web-cli_Tizen_Studio_6.1_macos-64.bin
   chmod +x web-cli_*.bin && ./web-cli_*.bin --accept-license ~/tizen-studio
   ```
   Apple Silicon precisa de Rosetta.
3. **Certificado:** TV até 2022 aceita o certificado padrão do Tizen — sem conta
   Samsung, sem DUID (medido na AU7700, Tizen 6.0, em 30/10/2026). A senha fica
   em `~/.config/tv-tuner/cert-pass`.
   ```sh
   T=~/tizen-studio/tools/ide/bin/tizen; P=$(cat ~/.config/tv-tuner/cert-pass)
   $T certificate -a tvtuner -p "$P" -c BR -n "Seu Nome" -f tvtuner
   $T security-profiles add -n tvtuner -a ~/tizen-studio-data/keystore/author/tvtuner.p12 -p "$P"
   ```
   TV 2023+ (Tizen 7+) exige certificado Samsung com o DUID da TV.

## Ciclo

```sh
./tv app build --profile tvtuner     # gera e assina tizen/TVTuner.wgt
./tv app install                     # instala por SDB (sem o sdb da Samsung) e abre
./tv app uninstall
```

Ver no navegador antes: abra `tizen/index.html` (modo demo, sem a API `tizen`).

O ID do pacote em `config.xml` precisa ter **exatamente 10** caracteres
alfanuméricos (`TvTunerHom`); com 9 a TV responde `install failed[118, -19],
reason: Parsing error`.
