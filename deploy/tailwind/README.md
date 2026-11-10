# Build do Tailwind CSS

O CSS do projeto é compilado com o **Tailwind CLI standalone** (`tailwindcss.exe`,
v4), sem depender de Node/npm. O executável já está em `deploy/tailwind/tailwindcss.exe`
(não versionado — baixar novamente se faltar, ver abaixo).

## Baixar o executável (se necessário)

```powershell
Invoke-WebRequest -Uri "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-windows-x64.exe" -OutFile "deploy\tailwind\tailwindcss.exe"
```

## Compilar

A partir da raiz do projeto:

```powershell
# build único
.\deploy\tailwind\tailwindcss.exe -i .\deploy\tailwind\input.css -o .\apps\core\static\core\css\tailwind.css --minify

# desenvolvimento (recompila ao salvar um template)
.\deploy\tailwind\tailwindcss.exe -i .\deploy\tailwind\input.css -o .\apps\core\static\core\css\tailwind.css --watch
```

O arquivo de saída (`apps/core/static/core/css/tailwind.css`) é **commitado no
git**, como o restante dos estáticos do projeto — não há pipeline de CI/build
no deploy atual (Whitenoise serve os arquivos já versionados). Sempre rodar o
build acima e commitar o CSS resultante após alterar templates ou
`deploy/tailwind/input.css`.

Depois de recompilar, rodar `python manage.py collectstatic` como de costume
antes de publicar em produção.

## Tokens de design

Cores, tipografia e componentes reutilizáveis (`.btn`, `.card`, `.badge-*`,
`.table-admin`, etc.) estão definidos em `deploy/tailwind/input.css`, com um
comentário de referência rápida no topo do arquivo.

### Componentes e tokens (identidade visual)

- **Cores:** `primary-*` (verde de marca), `success/warning/danger-*`, `info-*` (azul
  discreto para destaques neutros), `canvas` (fundo da página), `slate-*` (neutros).
- **Forma:** `rounded` = 8px (controles), `rounded-lg` = 12px (cartões, modais);
  `shadow-card` (cartões) e `shadow-pop` (modais e menus).
- **Botões:** `.btn` (primário), `.btn-secondary`, `.btn-danger` (contorno),
  `.btn-danger-solid`, `.btn-ghost`, `.btn-sm`, `.btn-segment` / `.btn-segment-ativo`.
- **Campos:** `.input-field` (input, select e textarea), `.field-hint`, `.field-error`.
- **Layout:** `.page-container`, `.page-header`, `.form-card*`, `.menu-item` / `.menu-item-danger`.
- **Densidade coletor → desktop** (grande no celular/Zebra, compacto em `lg`):
  `.btn-coletor`, `.btn-coletor-lg`, `.input-coletor`, `.texto-coletor`. Use no lugar de
  utilitários soltos como `max-lg:min-h-12` ou `lg:!text-sm`.
