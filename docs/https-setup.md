# HTTPS no WMS (Windows Server, sem IIS)

O Waitress (servidor WSGI usado em produção, ver `requirements-prod.txt`) só fala
HTTP puro — não termina TLS sozinho de forma confiável. Existem duas abordagens
neste repositório; escolha uma:

- **Opção A — Caddy como proxy reverso** (recomendada quando for possível baixar
  o Caddy): HTTPS na porta 443, redireciona HTTP→HTTPS, e o Waitress fica só em
  `127.0.0.1:8000` (nunca exposto na rede). Ver seção "Opção A" abaixo.
- **Opção B — HTTPS direto na porta 8000** (usar quando não for possível baixar
  o Caddy, ex.: bloqueio de firewall/proxy para `.exe` externos, mas `pip
  install` continua funcionando): troca o Waitress por `cheroot` (servidor WSGI
  Python com suporte TLS nativo, instalado via `pip`) servindo HTTPS
  diretamente na 8000, sem processo/porta adicional. Ver seção "Opção B"
  abaixo.

Ambas usam certificado autoassinado até a CA interna (`sitio-GOLD-CA`) voltar —
ver "Por que autoassinado por enquanto".

## Por que autoassinado por enquanto

A CA interna (`sitio-GOLD-CA`) que emitiria um certificado "confiável" pela rede
está com o `certsrv` fora do ar (mesma pendência documentada em
`wms/settings.py` para o certificado do AD/LDAPS — são dois certificados
diferentes, mesma causa raiz). Até isso ser resolvido, o Caddy gera um
certificado autoassinado próprio: o tráfego já fica **criptografado**, mas os
navegadores vão mostrar aviso de "conexão não seguível" até que o certificado
raiz do Caddy seja instalado como confiável nas máquinas clientes (opcional,
ver seção final). Quando a CA interna voltar, troque `tls internal` no
`Caddyfile` pelo certificado real (`tls /caminho/cert.pem /caminho/key.pem`).

## Opção A — Caddy como proxy reverso

Caddy foi escolhido em vez de Nginx/IIS porque roda como um único `.exe` sem
instalação, e gera e renova certificados sozinho (`tls internal` = certificado
autoassinado local).

1. **Instalar o Caddy**
   - Baixe o binário Windows (amd64) em https://caddyserver.com/download e
     salve como `C:\wms\caddy\caddy.exe` (ou local equivalente).

2. **Copiar o Caddyfile**
   - Use `deploy/Caddyfile` deste repositório como base. Confirme que os hosts
     (`192.168.1.120` no exemplo) batem com `DJANGO_ALLOWED_HOSTS` no `.env` real.

3. **Ativar os settings de segurança no `.env`**
   ```
   DJANGO_SECURE_SSL_REDIRECT=True
   DJANGO_USE_X_FORWARDED_PROTO=True
   DJANGO_CSRF_TRUSTED_ORIGINS=https://192.168.1.120
   ```
   (ajuste o IP/host conforme o ambiente real).

4. **Abrir portas no firewall do Windows**
   - Libere 443/tcp (HTTPS) e 80/tcp (redirect) para a LAN do armazém.
   - A porta 8000 (Waitress) **não** precisa — e não deve — ser exposta fora do
     próprio servidor, já que o Caddy é quem fala com o mundo externo.

5. **Registrar os dois processos como serviços Windows (NSSM)**
   O Waitress continua subindo do jeito já documentado em
   `docs/pendencias-producao.md`; adicione um segundo serviço para o Caddy:
   ```
   nssm install WMS-Waitress "C:\wms\WMS2\.venv\Scripts\waitress-serve.exe" --host=127.0.0.1 --port=8000 wms.wsgi:application
   nssm install WMS-Caddy "C:\wms\caddy\caddy.exe" run --config C:\wms\WMS2\deploy\Caddyfile
   ```
   Configure ambos para iniciar automaticamente e reiniciar em caso de falha.

6. **Testar**
   - `https://192.168.1.120` deve responder com o aviso de certificado
     autoassinado (esperado) e a tela de login do WMS.
   - `http://192.168.1.120` deve redirecionar para a versão HTTPS.
   - Confirme que login, formulários (CSRF) e a impressão ZPL continuam
     funcionando normalmente depois da mudança.

## Opção B — HTTPS direto na porta 8000 (sem Caddy)

Usa `cheroot` (`pip install -r requirements-prod.txt` já inclui) no lugar do
Waitress — mesma aplicação WSGI, mas com TLS nativo na própria porta 8000, via
`deploy/serve_https.py`. Não precisa abrir 443/80 nem instalar nada fora do
`pip`; a porta exposta na LAN continua sendo a 8000.

1. **Instalar dependências** (já inclui `cheroot` e `cryptography`)
   ```
   pip install -r requirements-prod.txt
   ```

2. **Gerar o certificado autoassinado** (uma vez; regenerar se o IP/hostname
   mudar ou quando expirar, validade padrão de 825 dias)
   ```
   python deploy\gerar_certificado.py 192.168.1.120
   ```
   Se o servidor também for acessado por um hostname DNS interno (ex.:
   `wms.cafedositio.com.br` apontando para o mesmo IP), gere o certificado
   cobrindo os dois, separados por vírgula, senão o navegador vai acusar
   "nome não corresponde ao certificado" ao acessar pelo hostname:
   ```
   python deploy\gerar_certificado.py 192.168.1.120,wms.cafedositio.com.br
   ```
   Gera `deploy\certs\wms.crt` e `deploy\certs\wms.key` — **não commitar** essa
   pasta (já está no `.gitignore`); é específica de cada servidor.

3. **Ativar os settings de segurança no `.env`**
   ```
   DJANGO_SECURE_SSL_REDIRECT=True
   DJANGO_USE_X_FORWARDED_PROTO=False
   DJANGO_CSRF_TRUSTED_ORIGINS=https://192.168.1.120
   ```
   `DJANGO_USE_X_FORWARDED_PROTO` fica `False` aqui porque não há proxy na
   frente — a conexão TLS chega direto no processo Python, então
   `request.is_secure()` já funciona sem depender de header repassado.

4. **Abrir a porta 8000 no firewall do Windows** para a LAN do armazém (ver
   comando `New-NetFirewallRule` já usado no projeto).

5. **Registrar como serviço Windows (NSSM)**, no lugar do serviço Waitress:
   ```
   nssm install WMS-HTTPS "C:\wms\WMS2\.venv\Scripts\python.exe" deploy\serve_https.py 0.0.0.0 8000
   ```
   Configure para iniciar automaticamente e reiniciar em caso de falha
   (mesma orientação de `docs/pendencias-producao.md`).

6. **Testar**
   - `https://192.168.1.120:8000` deve responder com o aviso de certificado
     autoassinado (esperado) e a tela de login do WMS.
   - Confirme login, formulários (CSRF) e impressão ZPL funcionando depois da
     mudança.

### Usar um hostname interno (ex.: `wms.cafedositio.com.br`) em vez do IP

Além do IP, o servidor pode ser acessado por um nome mais amigável via DNS
interno da empresa. Isso **não muda nada no código** — só requer três coisas,
a última fora deste repositório (ver `docs/pendencias-producao.md`):

1. `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` no `.env` já incluem
   `wms.cafedositio.com.br` além do IP (feito).
2. Gerar o certificado cobrindo os dois hosts (passo 2 acima).
3. **Criar o registro DNS** apontando `wms.cafedositio.com.br` para
   `192.168.1.120` — isso é feito no servidor DNS/AD da empresa (ex.: DNS
   Manager do Windows Server, zona `cafedositio.com.br` ou similar), **não**
   em nenhum arquivo deste projeto. Sem esse registro, o hostname não
   resolve e só o acesso por IP funciona.

Depois do registro DNS propagar, `https://wms.cafedositio.com.br:8000`
(Opção B) ou `https://wms.cafedositio.com.br` (Opção A, porta 443) deve
funcionar igual ao acesso por IP.

### Quando a CA interna voltar (Opção B)

Gere o certificado real via `certsrv` e substitua `deploy\certs\wms.crt` /
`wms.key` pelos arquivos emitidos pela CA (mesmo formato PEM), sem precisar
mexer no `serve_https.py`. Reinicie o serviço `WMS-HTTPS`.

## Confiar no certificado autoassinado (opcional, remove o aviso do navegador)

- **Opção A (Caddy)**: rode `caddy trust` no próprio servidor (exporta o
  certificado raiz do Caddy para o repositório de confiança do Windows local).
- **Opção B (`serve_https.py`)**: importe `deploy\certs\wms.crt` manualmente no
  repositório "Autoridades de Certificação Raiz Confiáveis" de cada máquina
  cliente (`certmgr.msc` ou `Import-Certificate` via PowerShell), já que não há
  um comando equivalente ao `caddy trust` aqui.

Em ambos os casos, distribuir para os computadores clientes que acessam o WMS
pela rede exige GPO do domínio — fora do escopo deste runbook; avaliar se vale
a pena dado que a CA interna deve resolver isso de forma definitiva quando
voltar.

## Quando a CA interna (sitio-GOLD-CA) voltar

**Opção A (Caddy)**:
1. Emitir um certificado para o host/IP do WMS via `certsrv`.
2. No `deploy/Caddyfile`, trocar `tls internal` por:
   ```
   tls C:\wms\certs\wms.crt C:\wms\certs\wms.key
   ```
3. Reiniciar o serviço `WMS-Caddy`.

**Opção B (`serve_https.py`)**: ver "Quando a CA interna voltar (Opção B)" acima.

Em ambas, os navegadores param de mostrar aviso assim que a cadeia da CA
interna já for confiável nas máquinas (GPO do domínio normalmente já cuida
disso).
