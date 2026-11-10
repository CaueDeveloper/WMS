"""Django settings do projeto WMS."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_dotenv(caminho):
    """Loader de .env (KEY=VALUE). Env vars reais têm prioridade sobre o arquivo."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding='utf-8').splitlines():
        linha = linha.strip()
        if not linha or linha.startswith('#') or '=' not in linha:
            continue
        chave, _, valor = linha.partition('=')
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_dotenv(BASE_DIR / '.env')


def _bool_env(nome, default):
    valor = os.environ.get(nome)
    if valor is None:
        return default
    return valor.strip().lower() in ('1', 'true', 'yes', 'on')


def _lista_env(nome):
    return [item.strip() for item in os.environ.get(nome, '').split(',') if item.strip()]


DEBUG = _bool_env('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError(
            'DJANGO_SECRET_KEY não configurada. Defina essa variável de ambiente '
            '(ou no .env) antes de rodar com DJANGO_DEBUG=False.'
        )
    SECRET_KEY = 'django-insecure-dev-only-fallback-key-do-not-use-in-production'
elif not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-')):
    raise RuntimeError('DJANGO_SECRET_KEY fraca: use ao menos 50 caracteres aleatórios.')

ALLOWED_HOSTS = _lista_env('DJANGO_ALLOWED_HOSTS')
if not ALLOWED_HOSTS:
    if not DEBUG:
        raise RuntimeError(
            'DJANGO_ALLOWED_HOSTS não configurada. Defina essa variável de ambiente '
            '(ou no .env) antes de rodar com DJANGO_DEBUG=False.'
        )
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.core',
    'apps.logistica',
    'apps.producao',
    'apps.romaneio',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'wms.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.menu',
            ],
        },
    },
]

WSGI_APPLICATION = 'wms.wsgi.application'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

# Impressora térmica Zebra ZT421 (RAW ZPL via TCP, porta 9100).
IMPRESSORA_ZPL_HOST = os.environ.get('IMPRESSORA_ZPL_HOST')
IMPRESSORA_ZPL_PORT = int(os.environ.get('IMPRESSORA_ZPL_PORT') or 9100)


# LDAP/Active Directory via django-auth-ldap. O import é protegido porque
# python-ldap não tem wheel oficial para Windows; sem ele, só ModelBackend.
try:
    import ldap
    from django_auth_ldap.config import GroupOfNamesType, LDAPSearch
    _LDAP_DISPONIVEL = True
except ImportError:
    _LDAP_DISPONIVEL = False

AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']

if _LDAP_DISPONIVEL:
    # O AD exige LDAPS (636). Com AUTH_LDAP_CA_CERTFILE a cadeia do certificado é
    # validada; sem ele, a validação fica desligada (PENDENTE: obter o certificado
    # da CA interna e definir essa variável no .env).
    _ldap_ca_certfile = os.environ.get('AUTH_LDAP_CA_CERTFILE', '')
    if _ldap_ca_certfile:
        ldap.set_option(ldap.OPT_X_TLS_CACERTFILE, _ldap_ca_certfile)
        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
    else:
        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)

    # Sem seguir referrals o AD rejeita as buscas mesmo após o bind.
    AUTH_LDAP_GLOBAL_OPTIONS = {ldap.OPT_REFERRALS: 0}

    AUTH_LDAP_SERVER_URI = os.environ.get('AUTH_LDAP_SERVER_URI', '')
    # user_ad/senha_ad são os nomes legados no .env; AUTH_LDAP_BIND_* têm prioridade.
    _ldap_bind_user = os.environ.get('AUTH_LDAP_BIND_DN') or os.environ.get('user_ad', '')
    _ldap_bind_user_domain = os.environ.get('AUTH_LDAP_BIND_USER_DOMAIN', '')
    AUTH_LDAP_BIND_DN = (
        f'{_ldap_bind_user}@{_ldap_bind_user_domain}'
        if _ldap_bind_user and _ldap_bind_user_domain and '@' not in _ldap_bind_user
        else _ldap_bind_user
    )
    AUTH_LDAP_BIND_PASSWORD = os.environ.get('AUTH_LDAP_BIND_PASSWORD') or os.environ.get('senha_ad', '')

    _ldap_user_search_base = os.environ.get('AUTH_LDAP_USER_SEARCH_BASE', '')
    _ldap_group_search_base = os.environ.get('AUTH_LDAP_GROUP_SEARCH_BASE', '')

    if AUTH_LDAP_SERVER_URI and _ldap_user_search_base and _ldap_group_search_base:
        AUTH_LDAP_USER_SEARCH = LDAPSearch(
            _ldap_user_search_base, ldap.SCOPE_SUBTREE, '(sAMAccountName=%(user)s)',
        )
        AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
            _ldap_group_search_base, ldap.SCOPE_SUBTREE, '(objectClass=group)',
        )
        AUTH_LDAP_GROUP_TYPE = GroupOfNamesType()

        AUTH_LDAP_USER_ATTR_MAP = {'first_name': 'givenName', 'last_name': 'sn', 'email': 'mail'}

        # DN do grupo AD -> grupos Django (aplicado em apps/core/ldap_signals.py).
        # DN vazio no .env = grupo nunca atribuído. Enquanto o AD só tem os grupos
        # por área, cada área mapeia para Operador + Gestor do setor.
        _grupo_dn_por_papel = {
            'AUTH_LDAP_GROUP_DN_ADMINISTRADORES': ['Administradores'],
            'AUTH_LDAP_GROUP_DN_OPERADOR_PRODUCAO': ['Operador Produção'],
            'AUTH_LDAP_GROUP_DN_OPERADOR_LOGISTICA': ['Operador Logística'],
            'AUTH_LDAP_GROUP_DN_GESTOR_PRODUCAO': ['Gestor Produção'],
            'AUTH_LDAP_GROUP_DN_GESTOR_LOGISTICA': ['Gestor Logística'],
            'AUTH_LDAP_GROUP_DN_PRODUCAO': ['Operador Produção', 'Gestor Produção'],
            'AUTH_LDAP_GROUP_DN_LOGISTICA': ['Operador Logística', 'Gestor Logística'],
        }
        AUTH_LDAP_GROUP_DN_MAP = {}
        for env_var, nomes_grupo in _grupo_dn_por_papel.items():
            dn = os.environ.get(env_var)
            if dn:
                AUTH_LDAP_GROUP_DN_MAP.setdefault(dn, []).extend(nomes_grupo)

        AUTH_LDAP_MIRROR_GROUPS = False
        AUTH_LDAP_FIND_GROUP_PERMS = False

        AUTHENTICATION_BACKENDS = [
            'django_auth_ldap.backend.LDAPBackend',
            'django.contrib.auth.backends.ModelBackend',
        ]


# Produção usa MySQL via .env; sem DJANGO_DB_NAME cai no SQLite de desenvolvimento.
_db_name = os.environ.get('DJANGO_DB_NAME')
if _db_name:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': _db_name,
            'USER': os.environ.get('DJANGO_DB_USER', ''),
            'PASSWORD': os.environ.get('DJANGO_DB_PASSWORD', ''),
            'HOST': os.environ.get('DJANGO_DB_HOST', 'localhost'),
            'PORT': os.environ.get('DJANGO_DB_PORT', '3306'),
            'CONN_MAX_AGE': 60,
            'OPTIONS': {
                'charset': 'utf8mb4',
                'sql_mode': 'STRICT_ALL_TABLES',
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 10},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Hasher rápido só na suíte de testes, e nunca com DEBUG desligado.
if 'test' in sys.argv and DEBUG:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Endurecimento de sessão/cookies e cabeçalhos (valem também em DEBUG).
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Produção: tráfego interno, então HTTPS/HSTS só ligam com DJANGO_SECURE_SSL_REDIRECT=True.
if not DEBUG:
    CSRF_TRUSTED_ORIGINS = _lista_env('DJANGO_CSRF_TRUSTED_ORIGINS')

    SECURE_SSL_REDIRECT = _bool_env('DJANGO_SECURE_SSL_REDIRECT', False)
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
    SECURE_HSTS_SECONDS = 3600 if SECURE_SSL_REDIRECT else 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
    SECURE_HSTS_PRELOAD = SECURE_SSL_REDIRECT

    # Só ligar se o proxy na frente define X-Forwarded-Proto; caso contrário o
    # cabeçalho pode ser forjado pelo cliente.
    if _bool_env('DJANGO_USE_X_FORWARDED_PROTO', False):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# SAP Business One (Service Layer) — somente leitura: o WMS consulta OPs e estoque, nunca grava.
SAP_B1_BASE_URL = os.environ.get('SAP_B1_BASE_URL', '').rstrip('/')
SAP_B1_COMPANY_DB = os.environ.get('SAP_B1_COMPANY_DB', '')
SAP_B1_USERNAME = os.environ.get('SAP_B1_USERNAME', '')
SAP_B1_PASSWORD = os.environ.get('SAP_B1_PASSWORD', '')
SAP_B1_TIMEOUT = int(os.environ.get('SAP_B1_TIMEOUT_MS', '30000')) / 1000
SAP_B1_VERIFY_TLS = _bool_env('SAP_B1_REJECT_UNAUTHORIZED', True)
