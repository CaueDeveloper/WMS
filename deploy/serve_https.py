"""Serve o WMS via HTTPS direto na porta 8000, sem proxy reverso.

Alternativa ao Waitress+Caddy (docs/https-setup.md) para ambientes onde não é
possível baixar/instalar o Caddy. Usa cheroot (servidor WSGI com suporte TLS
nativo) para terminar HTTPS na própria porta que a aplicação escuta.

Pré-requisito: gerar o certificado antes (uma vez, ou quando expirar/mudar o IP):
    python deploy/gerar_certificado.py <ip_ou_host>

Uso:
    python deploy/serve_https.py [host] [porta]

Exemplo:
    python deploy/serve_https.py 0.0.0.0 8000
"""
import sys
from pathlib import Path

from cheroot.server import HTTPServer
from cheroot.ssl.builtin import BuiltinSSLAdapter
from cheroot.wsgi import Server as WSGIServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django  # noqa: E402

import os  # noqa: E402

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wms.settings')
django.setup()

from wms.wsgi import application  # noqa: E402

CERTS_DIR = Path(__file__).resolve().parent / 'certs'
CERT_FILE = CERTS_DIR / 'wms.crt'
KEY_FILE = CERTS_DIR / 'wms.key'


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else '0.0.0.0'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    if not CERT_FILE.exists() or not KEY_FILE.exists():
        raise SystemExit(
            f'Certificado não encontrado em {CERTS_DIR}. Gere antes com:\n'
            f'  python deploy/gerar_certificado.py <ip_ou_host>'
        )

    servidor = WSGIServer((host, port), application, numthreads=10)
    servidor.ssl_adapter = BuiltinSSLAdapter(str(CERT_FILE), str(KEY_FILE))

    print(f'Servindo HTTPS em https://{host}:{port}')
    try:
        servidor.start()
    except KeyboardInterrupt:
        servidor.stop()


if __name__ == '__main__':
    main()
