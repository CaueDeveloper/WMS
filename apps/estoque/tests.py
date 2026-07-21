import threading
import time

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.estoque.models import Endereco
from apps.estoque.services import EnderecoCheioError, alocar_palete
from apps.recebimento.models import Palete


class GerarEstruturaArmazemTests(TestCase):

    def test_cria_exatamente_60_enderecos(self):
        call_command('gerar_estrutura_armazem')
        self.assertEqual(Endereco.objects.count(), 60)

    def test_codigos_seguem_padrao_cc_a_e_sao_unicos(self):
        call_command('gerar_estrutura_armazem')
        codigos = set(Endereco.objects.values_list('codigo', flat=True))
        esperados = {f'{corredor:02d}-{andar}' for corredor in range(1, 21) for andar in ('A', 'B', 'C')}
        self.assertEqual(codigos, esperados)

    def test_todos_iniciam_com_capacidade_21_e_ocupacao_zero(self):
        call_command('gerar_estrutura_armazem')
        self.assertFalse(
            Endereco.objects.exclude(capacidade=21, ocupacao_atual=0).exists()
        )

    def test_comando_e_idempotente(self):
        call_command('gerar_estrutura_armazem')
        call_command('gerar_estrutura_armazem')
        self.assertEqual(Endereco.objects.count(), 60)


def _criar_palete_conferido(sufixo):
    return Palete.objects.create(
        sku=f'CAFE-{sufixo}',
        lote=f'L-{sufixo}',
        descricao='Café teste',
        peso_kg='20.000',
        estado='CONFERIDO',
    )


class AlocarPaleteCapacidadeTests(TestCase):

    def setUp(self):
        self.endereco = Endereco.objects.create(corredor=2, andar='B', codigo='02-B', capacidade=21, ocupacao_atual=0)

    def test_permite_alocar_ate_21_paletes(self):
        for i in range(21):
            alocar_palete(_criar_palete_conferido(f'OK{i}'), '02-B')

        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.endereco, estado='ARMAZENADO').count(), 21)

    def test_22o_palete_e_rejeitado(self):
        for i in range(21):
            alocar_palete(_criar_palete_conferido(f'CHEIO{i}'), '02-B')

        vigesimo_segundo = _criar_palete_conferido('EXTRA')
        with self.assertRaises(EnderecoCheioError):
            alocar_palete(vigesimo_segundo, '02-B')

        self.endereco.refresh_from_db()
        vigesimo_segundo.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(vigesimo_segundo.estado, 'CONFERIDO')
        self.assertIsNone(vigesimo_segundo.endereco)


class AlocarPaleteConcorrenciaTests(TransactionTestCase):
    """Simula requisições paralelas disputando a última vaga de um endereço."""

    def setUp(self):
        self.endereco = Endereco.objects.create(corredor=3, andar='C', codigo='03-C', capacidade=21, ocupacao_atual=20)

    def test_apenas_um_vencedor_para_a_ultima_vaga(self):
        NUM_CONCORRENTES = 5
        paletes = [_criar_palete_conferido(f'RACE{i}') for i in range(NUM_CONCORRENTES)]
        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)

        def tentar_alocar(palete):
            barreira.wait()
            # SQLite não bloqueia-e-espera por linha como Postgres/MySQL: sob disputa,
            # a transação perdedora recebe "database is locked" na hora em vez de
            # aguardar o commit da vencedora. Retentar aqui simula essa espera, para
            # que a rejeição observada seja sempre a regra de negócio (EnderecoCheioError),
            # como aconteceria de fato em produção (MySQL).
            try:
                for _ in range(100):
                    try:
                        alocar_palete(palete, '03-C')
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except EnderecoCheioError:
                resultados.append('rejeitado')
            finally:
                connection.close()

        threads = [threading.Thread(target=tentar_alocar, args=(p,)) for p in paletes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.endereco.refresh_from_db()
        self.assertEqual(resultados.count('sucesso'), 1)
        self.assertEqual(resultados.count('rejeitado'), NUM_CONCORRENTES - 1)
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.endereco, estado='ARMAZENADO').count(), 1)


class PutAwayViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador2', password='senha-teste-123')
        self.client.login(username='operador2', password='senha-teste-123')
        self.endereco = Endereco.objects.create(corredor=4, andar='A', codigo='04-A', capacidade=21, ocupacao_atual=0)
        self.palete = _criar_palete_conferido('VIEW1')

    def test_lista_paletes_conferidos_e_enderecos_disponiveis(self):
        response = self.client.get(reverse('estoque:putaway'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.palete.sku)
        self.assertContains(response, '04-A')

    def test_endereco_cheio_nao_aparece_na_lista_de_disponiveis(self):
        self.endereco.ocupacao_atual = 21
        self.endereco.save()
        response = self.client.get(reverse('estoque:putaway'))
        self.assertNotContains(response, '04-A')

    def test_palete_armazenado_nao_aparece_mais_na_lista(self):
        outro = _criar_palete_conferido('VIEW2')
        outro.estado = 'ARMAZENADO'
        outro.save()
        response = self.client.get(reverse('estoque:putaway'))
        self.assertNotContains(response, outro.barcode_palete)

    def test_selecionar_endereco_armazena_palete(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'palete_id': self.palete.pk,
            'codigo_endereco': '04-A',
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(self.palete.estado, 'ARMAZENADO')
        self.assertEqual(self.palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

    def test_endereco_lotado_entre_a_listagem_e_o_envio_ainda_e_rejeitado(self):
        # Defesa em profundidade: mesmo que o endereço tenha ficado cheio depois
        # de a lista ser mostrada, o servidor não pode confiar só no <select>.
        self.endereco.ocupacao_atual = 21
        self.endereco.save()

        response = self.client.post(reverse('estoque:putaway'), {
            'palete_id': self.palete.pk,
            'codigo_endereco': '04-A',
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertIsNone(self.palete.endereco)

    def test_palete_id_invalido_retorna_404(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'palete_id': 999999,
            'codigo_endereco': '04-A',
        })
        self.assertEqual(response.status_code, 404)

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:putaway'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:putaway')}")


class MapaEstoqueTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador4', password='senha-teste-123')
        self.client.login(username='operador4', password='senha-teste-123')

    def test_mostra_grade_com_todos_os_enderecos_cadastrados(self):
        Endereco.objects.create(corredor=1, andar='A', codigo='01-A', capacidade=21, ocupacao_atual=5)
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '01-A')
        self.assertContains(response, '5/21')

    def test_endereco_nao_cadastrado_aparece_como_vazio(self):
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertContains(response, 'nivel-vazio')

    def test_lista_paletes_armazenados_com_endereco(self):
        endereco = Endereco.objects.create(corredor=6, andar='B', codigo='06-B', capacidade=21, ocupacao_atual=1)
        palete = _criar_palete_conferido('MAPA1')
        palete.estado = 'ARMAZENADO'
        palete.endereco = endereco
        palete.save()

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertContains(response, palete.barcode_palete)
        self.assertContains(response, '06-B')

    def test_totais_de_ocupacao_sao_somados_corretamente(self):
        Endereco.objects.create(corredor=7, andar='A', codigo='07-A', capacidade=21, ocupacao_atual=10)
        Endereco.objects.create(corredor=7, andar='B', codigo='07-B', capacidade=21, ocupacao_atual=5)

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.context['ocupacao_total'], 15)
        self.assertEqual(response.context['capacidade_total'], 42)
        self.assertEqual(response.context['percentual_geral'], round(15 / 42 * 100))

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:mapa_estoque')}")
