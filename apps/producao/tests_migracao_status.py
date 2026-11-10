import importlib

from django.apps import apps
from django.test import TestCase

from apps.producao.models import Palete, PaleteHistorico

migracao = importlib.import_module('apps.producao.migrations.0017_renomear_status_palete')


class RenomearStatusTests(TestCase):
    def test_codigos_antigos_viram_novos_em_palete_e_historico(self):
        for antigo, novo in migracao.RENOMEAR.items():
            p = Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1, estado=antigo)
            PaleteHistorico.objects.create(palete=p, estado=antigo)
            migracao.para_novos(apps, None)
            p.refresh_from_db()
            self.assertEqual(p.estado, novo)
            self.assertEqual(p.historico.get().estado, novo)

    def test_reversa_volta_aos_codigos_antigos(self):
        p = Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1, estado='APROVADO_PRODUCAO')
        migracao.para_antigos(apps, None)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'CONFERIDO')
