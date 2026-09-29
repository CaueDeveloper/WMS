"""Gera um certificado autoassinado para o WMS servir HTTPS direto na porta 8000.

Uso:
    python deploy/gerar_certificado.py <host1>[,<host2>,...] [dias_validade]

Exemplo (um único host):
    python deploy/gerar_certificado.py 192.168.1.120

Exemplo (IP + hostname DNS interno, mesmo servidor):
    python deploy/gerar_certificado.py 192.168.1.120,wms.cafedositio.com.br

Gera deploy/certs/wms.crt e deploy/certs/wms.key, com todos os hosts listados
no Subject Alternative Name do certificado — necessário para o navegador
aceitar o certificado tanto acessando pelo IP quanto pelo hostname. Certificado
autoassinado: os navegadores vão mostrar aviso de "conexão não segura" até o
certificado raiz ser instalado como confiável nas máquinas clientes (ver
docs/https-setup.md) ou até a CA interna (sitio-GOLD-CA) emitir um certificado
real — o tráfego já vai criptografado mesmo com o aviso.
"""
import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _subject_alt_name(host):
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def gerar(hosts, dias_validade=825, saida_dir=None):
    if isinstance(hosts, str):
        hosts = [hosts]
    saida_dir = saida_dir or Path(__file__).resolve().parent / 'certs'
    saida_dir.mkdir(parents=True, exist_ok=True)

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    agora = datetime.datetime.now(datetime.timezone.utc)

    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=dias_validade))
        .add_extension(
            x509.SubjectAlternativeName([_subject_alt_name(h) for h in hosts]), critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True,
        )
        .sign(chave, hashes.SHA256())
    )

    caminho_key = saida_dir / 'wms.key'
    caminho_crt = saida_dir / 'wms.crt'

    caminho_key.write_bytes(
        chave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    caminho_crt.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))

    print(f'Certificado gerado para {hosts}, válido por {dias_validade} dias:')
    print(f'  {caminho_crt}')
    print(f'  {caminho_key}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    hosts = [h.strip() for h in sys.argv[1].split(',') if h.strip()]
    dias = int(sys.argv[2]) if len(sys.argv) > 2 else 825
    gerar(hosts, dias)
