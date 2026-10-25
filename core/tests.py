from datetime import date, timedelta
from concurrent.futures import Future
import os
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .admin import EquipeAdmin, TreinoAdmin
from .models import Atleta, Equipe, NotificacaoLog, Produto, Treino
from .price_search import search_product_prices
from .rpa import enviar_email_gmail, enviar_whatsapp


User = get_user_model()


class ApiPlatformTests(APITestCase):
    def setUp(self):
        self.trainer = User.objects.create_user(
            username='personal',
            password='trainer-pass-123',
            is_staff=True,
            is_superuser=True,
        )
        self.alice_user = User.objects.create_user(username='alice', password='runner-pass-123')
        self.bob_user = User.objects.create_user(username='bob', password='runner-pass-456')
        self.alice = Atleta.objects.create(
            usuario=self.alice_user,
            nome='Alice Runner',
            email='alice@example.com',
            whatsapp='5521999990000',
            objetivo='10 km',
        )
        self.bob = Atleta.objects.create(
            usuario=self.bob_user,
            nome='Bob Runner',
            email='bob@example.com',
            whatsapp='5521999991111',
            objetivo='Meia maratona',
        )
        self.workout = Treino.objects.create(
            atleta=self.alice,
            data=date(2026, 10, 25),
            tipo='longao',
            distancia_km='15.00',
            pace_alvo='5:45 min/km',
            observacoes='Ritmo confortável',
        )

    def test_token_login_returns_token_and_role(self):
        response = self.client.post(
            reverse('api-token-auth'),
            {'username': 'alice', 'password': 'runner-pass-123'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['token'])
        self.assertFalse(response.data['is_staff'])

    def test_athlete_can_only_read_own_records(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/atletas/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [self.alice.id])

        response = self.client.get(f'/api/treinos/{self.workout.id + 100}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_trainer_can_create_and_read_teams(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.post('/api/equipes/', {
            'nome': 'Equipe de corrida',
            'descricao': 'Grupo de preparação',
            'atletas': [self.alice.id, self.bob.id],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['atletas'], [self.alice.id, self.bob.id])
        self.assertEqual(len(response.data['atletas_detalhes']), 2)

        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/equipes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], Equipe.objects.get(nome='Equipe de corrida').id)

    def test_team_admin_save_creates_workouts_and_queues_both_notifications(self):
        self.client.force_login(self.trainer)
        admin_url = reverse('admin:core_equipe_add')
        with (
            patch('core.views.rpa_executor.submit') as submit,
            self.captureOnCommitCallbacks(execute=True),
        ):
            submit.return_value = Future()
            response = self.client.post(admin_url, {
                'nome': 'Equipe manhã',
                'descricao': 'Treino coletivo',
                'atletas': [self.alice.pk, self.bob.pk],
                'enviar_prescricao': 'on',
                'data': '2026-10-26',
                'tipo': 'intervalado',
                'distancia_km': '8.00',
                'pace_alvo': '5:30',
                'repeticoes_tiro': '8x 400m',
                'pace_tiro': '4:15 min/km',
                'tipo_descanso': 'ativo',
                'pace_descanso': '6:30 min/km',
                'observacoes': 'Treino coletivo',
                '_save': 'Salvar',
            }, follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        equipe = Equipe.objects.get(nome='Equipe manhã')
        self.assertSetEqual(set(equipe.atletas.all()), {self.alice, self.bob})
        for atleta in (self.alice, self.bob):
            treino = atleta.treinos.get(data=date(2026, 10, 26))
            self.assertEqual(treino.repeticoes_tiro, '8x 400m')
            self.assertEqual(treino.tipo, 'intervalado')
        self.assertEqual(submit.call_count, 2)
        self.assertTrue(all(call.args[0].__name__ == '_dispatch_notifications' for call in submit.call_args_list))
        self.assertEqual(
            {call.args[1] for call in submit.call_args_list},
            {self.alice.pk, self.bob.pk},
        )
        for call in submit.call_args_list:
            summary = call.args[5]
            self.assertIn('Olá, ', summary)
            self.assertIn(
                'Você tem uma nova prescrição de treino para a Equipe: Equipe manhã '
                'agendada para o dia 26/10/2026:',
                summary,
            )
            self.assertIn('- Repetições: 8x 400m', summary)
            self.assertIn('- Ritmo do Tiro: 4:15 min/km', summary)
            self.assertIn('- Tipo de Descanso: Ativo (Trote / Caminhada)', summary)
            self.assertIn('- Pace / Tempo de Descanso: 6:30 min/km', summary)
            self.assertIn('Observações do Coach: Treino coletivo', summary)
        self.assertContains(response, 'envios de E-mail e WhatsApp')

    def test_team_admin_save_without_confirmation_does_not_send_or_create_workouts(self):
        self.client.force_login(self.trainer)
        with patch('core.views.rpa_executor.submit') as submit:
            response = self.client.post(reverse('admin:core_equipe_add'), {
                'nome': 'Equipe sem envio',
                'atletas': [self.alice.pk],
                '_save': 'Salvar',
            })
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        equipe = Equipe.objects.get(nome='Equipe sem envio')
        self.assertEqual(equipe.atletas.count(), 1)
        self.assertEqual(self.alice.treinos.count(), 1)
        submit.assert_not_called()

    def test_team_admin_save_requires_workout_details_when_sending(self):
        self.client.force_login(self.trainer)
        response = self.client.post(reverse('admin:core_equipe_add'), {
            'nome': 'Equipe sem treino válido',
            'atletas': [self.alice.pk],
            'enviar_prescricao': 'on',
            '_save': 'Salvar',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Equipe.objects.filter(nome='Equipe sem treino válido').exists())

    def test_team_admin_exposes_horizontal_atleta_selector_without_manual_actions(self):
        self.assertEqual(EquipeAdmin.filter_horizontal, ('atletas',))
        self.assertEqual(EquipeAdmin.actions, ())

    def test_trainer_can_create_athlete_login_and_profile(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.post('/api/atletas/', {
            'nome': 'Carla Runner',
            'email': 'carla@example.com',
            'whatsapp': '5521999992222',
            'nivel': 'iniciante',
            'objetivo': '5 km',
            'username': 'carla',
            'password': 'initial-pass-123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_user = User.objects.get(username='carla')
        self.assertTrue(new_user.check_password('initial-pass-123'))
        self.assertEqual(response.data['login'], 'carla')

    def test_trainer_can_update_and_delete_athlete_and_login(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.patch(
            f'/api/atletas/{self.alice.id}/',
            {'objetivo': 'Meia maratona'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['objetivo'], 'Meia maratona')
        self.assertEqual(
            self.client.delete(f'/api/atletas/{self.alice.id}/').status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertFalse(User.objects.filter(pk=self.alice_user.pk).exists())

    def test_athlete_cannot_create_training(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.post('/api/treinos/', {
            'atleta': self.alice.id,
            'data': '2026-10-26',
            'tipo': 'continuo',
            'distancia_km': '6.00',
            'pace_alvo': '5:40',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_filters_and_completion_action(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/treinos/?tipo=longao&status=pendente')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [self.workout.id])

        response = self.client.post(f'/api/treinos/{self.workout.id}/concluir/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.workout.refresh_from_db()
        self.assertEqual(self.workout.status, 'concluido')

    def test_interval_training_details_are_available_in_api_and_admin(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.post('/api/treinos/', {
            'atleta': self.alice.id,
            'data': '2026-10-26',
            'tipo': 'intervalado',
            'distancia_km': '8.00',
            'pace_alvo': '5:30',
            'repeticoes_tiro': '8x 400m',
            'pace_tiro': '4:15 min/km',
            'tipo_descanso': 'ativo',
            'pace_descanso': '6:30 min/km',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['repeticoes_tiro'], '8x 400m')
        self.assertEqual(response.data['pace_tiro'], '4:15 min/km')
        self.assertEqual(response.data['tipo_descanso'], 'ativo')
        self.assertEqual(response.data['tipo_descanso_display'], 'Ativo (Trote / Caminhada)')
        self.assertEqual(response.data['pace_descanso'], '6:30 min/km')
        fieldsets = dict(TreinoAdmin.fieldsets)
        self.assertIn('Detalhamento de Intervalado / Tiros', fieldsets)
        self.assertEqual(
            fieldsets['Detalhamento de Intervalado / Tiros']['fields'],
            ('repeticoes_tiro', 'pace_tiro', 'tipo_descanso', 'pace_descanso'),
        )

    def test_chatbot_includes_interval_paces_in_workout_message(self):
        self.client.force_authenticate(self.alice_user)
        self.workout.tipo = 'intervalado'
        self.workout.repeticoes_tiro = '8x 400m'
        self.workout.pace_tiro = '4:15 min/km'
        self.workout.tipo_descanso = 'passivo'
        self.workout.pace_descanso = '1 min parado'
        self.workout.save(update_fields=[
            'tipo',
            'repeticoes_tiro',
            'pace_tiro',
            'tipo_descanso',
            'pace_descanso',
        ])
        response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'iniciar', 'reset': True},
            format='json',
        )
        self.assertEqual(response.data['treino']['repeticoes_tiro'], '8x 400m')
        self.assertEqual(response.data['treino']['pace_tiro'], '4:15 min/km')
        self.assertEqual(response.data['treino']['tipo_descanso'], 'passivo')
        self.assertIn('Detalhamento dos tiros:', response.data['mensagem'])
        self.assertIn('- Repetições: 8x 400m', response.data['mensagem'])
        self.assertIn('- Ritmo do tiro: 4:15 min/km', response.data['mensagem'])
        self.assertIn('- Descanso: Passivo (Parado) — 1 min parado', response.data['mensagem'])
        self.assertEqual(
            response.data['opcoes'],
            [
                {'label': '🔥 Dica de Aquecimento', 'payload': 'aquecimento'},
                {'label': '🧘 Alongamento Pós-Treino', 'payload': 'alongamento'},
                {'label': '🛍️ Produtos Cadastrados', 'payload': 'listar_produtos'},
            ],
        )

    def test_product_crud_and_student_scope(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.post('/api/produtos/', {
            'atleta': self.alice.id,
            'nome_produto': 'Gel de carboidrato',
            'categoria': 'suplemento',
            'preco': '12.50',
            'descricao': 'Usar em treinos longos.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        product_id = response.data['id']

        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/produtos/')
        self.assertEqual([item['id'] for item in response.data], [product_id])
        self.assertEqual(self.client.delete(f'/api/produtos/{product_id}/').status_code, status.HTTP_403_FORBIDDEN)

    def test_global_products_are_visible_to_every_student(self):
        global_product = Produto.objects.create(
            atleta=None,
            nome_produto='Repositor eletrolítico',
            categoria='suplemento',
            preco='18.90',
            descricao='Recomendação geral.',
        )
        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/produtos/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [global_product.id])

        self.client.force_authenticate(self.bob_user)
        response = self.client.get('/api/produtos/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [global_product.id])

    def test_product_can_be_created_without_an_athlete(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.post('/api/produtos/', {
            'nome_produto': 'Produto para todos',
            'categoria': 'outro',
            'preco': '5.00',
            'descricao': 'Cadastro global.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['atleta'])
        product = Produto.objects.get(pk=response.data['id'])
        self.assertIn('Global', str(product))

    def test_student_only_sees_active_global_and_own_products(self):
        global_product = Produto.objects.create(
            atleta=None,
            nome_produto='Gel global',
            categoria='suplemento',
            preco='10.00',
        )
        own_product = Produto.objects.create(
            atleta=self.alice,
            nome_produto='Tênis recomendado',
            categoria='equipamento',
            preco='250.00',
        )
        Produto.objects.create(
            atleta=self.bob,
            nome_produto='Produto de outro aluno',
            categoria='outro',
            preco='5.00',
        )
        Produto.objects.create(
            atleta=None,
            nome_produto='Produto inativo',
            categoria='outro',
            preco='1.00',
            ativo=False,
        )

        self.client.force_authenticate(self.alice_user)
        response = self.client.get('/api/produtos/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {product['id'] for product in response.data},
            {global_product.id, own_product.id},
        )

    def test_trainer_can_update_and_delete_training_and_notification_logs(self):
        self.client.force_authenticate(self.trainer)
        response = self.client.patch(
            f'/api/treinos/{self.workout.id}/',
            {'observacoes': 'Atualizado pelo personal'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['observacoes'], 'Atualizado pelo personal')
        self.assertEqual(
            self.client.delete(f'/api/treinos/{self.workout.id}/').status_code,
            status.HTTP_204_NO_CONTENT,
        )

        response = self.client.post('/api/notificacoes/', {
            'atleta': self.alice.id,
            'canal': 'email',
            'status_envio': 'sucesso',
            'mensagem': 'Teste administrativo',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notification_id = response.data['id']
        response = self.client.patch(
            f'/api/notificacoes/{notification_id}/',
            {'status_envio': 'falha'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_envio'], 'falha')
        self.assertEqual(
            self.client.delete(f'/api/notificacoes/{notification_id}/').status_code,
            status.HTTP_204_NO_CONTENT,
        )

    @patch('core.views.enviar_whatsapp')
    @patch('core.views.enviar_email_gmail')
    @patch('core.views.rpa_executor.submit')
    @patch('core.views.close_old_connections')
    def test_rpa_sends_and_records_both_channels(self, close_connections, submit, send_email, send_whatsapp):
        future = Future()
        submit.return_value = future
        self.client.force_authenticate(self.alice_user)
        response = self.client.post('/api/treinos/enviar-rpa/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], 'enfileirado')
        function, *args = submit.call_args.args
        function(*args)
        future.set_result(None)
        self.assertEqual(NotificacaoLog.objects.filter(atleta=self.alice, status_envio='sucesso').count(), 2)
        self.assertIn('15.00 km', send_whatsapp.call_args.args[1])
        send_email.assert_called_once()
        subject = send_email.call_args.args[1]
        body = send_email.call_args.args[2]
        self.assertEqual(subject, 'RUN_LAB - Prescrição de Treino: Longão (25/10/2026)')
        self.assertIn('Olá, Alice Runner!', body)
        self.assertNotIn('Equipe:', body)
        self.assertIn('Segue a sua prescrição de treino para o dia 25/10/2026:', body)
        self.assertNotIn('Detalhamento das Séries / Tiros', body)
        self.assertNotIn('None', body)

    @patch('core.views.enviar_whatsapp')
    @patch('core.views.enviar_email_gmail')
    @patch('core.views.rpa_executor.submit')
    @patch('core.views.close_old_connections')
    def test_interval_workout_email_includes_only_filled_series_details(
        self,
        close_connections,
        submit,
        send_email,
        send_whatsapp,
    ):
        self.workout.tipo = 'intervalado'
        self.workout.data = date(2026, 10, 30)
        self.workout.distancia_km = '10.00'
        self.workout.pace_alvo = '3:00 min/km'
        self.workout.repeticoes_tiro = '8x 400m'
        self.workout.pace_tiro = '4:15 min/km'
        self.workout.tipo_descanso = 'ativo'
        self.workout.pace_descanso = '6:30 min/km'
        self.workout.observacoes = 'Gás!!!'
        self.workout.save()

        future = Future()
        submit.return_value = future
        self.client.force_authenticate(self.alice_user)
        response = self.client.post('/api/treinos/enviar-rpa/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        function, *args = submit.call_args.args
        function(*args)
        future.set_result(None)

        send_email.assert_called_once()
        recipient, subject, body = send_email.call_args.args
        self.assertEqual(recipient, self.alice.email)
        self.assertEqual(
            subject,
            'RUN_LAB - Prescrição de Treino: Intervalado / Tiros (30/10/2026)',
        )
        self.assertIn('Olá, Alice Runner!', body)
        self.assertIn('Segue a sua prescrição de treino para o dia 30/10/2026:', body)
        self.assertIn('- Tipo: Intervalado / Tiros', body)
        self.assertIn('- Distância Total: 10.00 km', body)
        self.assertIn('- Pace Alvo Geral: 3:00 min/km', body)
        self.assertIn('Detalhamento das Séries / Tiros:', body)
        self.assertIn('- Repetições: 8x 400m', body)
        self.assertIn('- Ritmo do Tiro: 4:15 min/km', body)
        self.assertIn('- Tipo de Descanso: Ativo (Trote / Caminhada)', body)
        self.assertIn('- Pace / Tempo de Descanso: 6:30 min/km', body)
        self.assertIn('Observações do Coach: Gás!!!', body)
        self.assertNotIn('None', body)
        self.assertEqual(send_whatsapp.call_args.args[1], body)

    def test_interval_email_omits_empty_detail_lines(self):
        self.workout.tipo = 'intervalado'
        self.workout.repeticoes_tiro = ''
        self.workout.pace_tiro = None
        self.workout.tipo_descanso = None
        self.workout.pace_descanso = ''
        self.workout.observacoes = ''
        self.workout.save()

        from .views import _format_workout_notification

        subject, body = _format_workout_notification(self.alice, self.workout)
        self.assertIn('Intervalado / Tiros', subject)
        self.assertNotIn('Detalhamento das Séries / Tiros', body)
        self.assertNotIn('Repetições:', body)
        self.assertNotIn('Ritmo do Tiro:', body)
        self.assertNotIn('Tipo de Descanso:', body)
        self.assertNotIn('Pace / Tempo de Descanso:', body)
        self.assertNotIn('Observações do Coach:', body)
        self.assertNotIn('None', body)

    @patch('core.views.enviar_whatsapp')
    @patch('core.views.enviar_email_gmail', side_effect=RuntimeError('SMTP indisponível'))
    @patch('core.views.rpa_executor.submit')
    @patch('core.views.close_old_connections')
    def test_rpa_failure_is_logged_and_reported(self, close_connections, submit, send_email, send_whatsapp):
        send_whatsapp.side_effect = RuntimeError('WhatsApp indisponível')
        future = Future()
        submit.return_value = future
        self.client.force_authenticate(self.alice_user)
        response = self.client.post('/api/treinos/enviar-ultimo/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        function, *args = submit.call_args.args
        function(*args)
        future.set_result(None)
        self.assertEqual(NotificacaoLog.objects.filter(atleta=self.alice, status_envio='falha').count(), 2)
        self.assertFalse(NotificacaoLog.objects.filter(atleta=self.alice, status_envio='sucesso').exists())

    def test_rpa_returns_not_found_without_workout(self):
        self.workout.delete()
        self.client.force_authenticate(self.alice_user)
        response = self.client.post('/api/treinos/enviar-ultimo/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('core.views.search_product_prices')
    def test_chatbot_lists_recommendations_and_quotes_only_selected_product(self, price_search):
        self.client.force_authenticate(self.alice_user)
        global_product = Produto.objects.create(
            atleta=None,
            nome_produto='Gel de carboidrato',
            categoria='suplemento',
            preco='12.50',
            descricao='Consumir conforme orientação profissional.',
        )
        own_product = Produto.objects.create(
            atleta=self.alice,
            nome_produto='Camisa',
            categoria='equipamento',
            preco='89.90',
        )
        Produto.objects.create(
            atleta=self.bob,
            nome_produto='Produto privado',
            categoria='outro',
            preco='1.00',
        )
        Produto.objects.create(
            atleta=None,
            nome_produto='Produto desativado',
            categoria='outro',
            preco='1.00',
            ativo=False,
        )
        response = self.client.post('/api/chatbot/', {'mensagem': 'Quero conferir meu treino de hoje'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['intent'], 'treino')
        self.assertIn('Olá Alice!', response.data['resposta'])
        self.assertIn('Longão de 15.00 km', response.data['resposta'])
        self.assertIn('pace alvo de 5:45 min/km', response.data['resposta'])
        self.assertIn('Como está se sentindo para essa sessão?', response.data['resposta'])
        self.assertEqual(response.data['mensagem'], response.data['resposta'])
        self.assertEqual(
            [option['payload'] for option in response.data['opcoes']],
            ['aquecimento', 'alongamento', 'hidratacao', 'listar_produtos'],
        )
        self.assertEqual(response.data['treino']['id'], self.workout.id)
        self.assertEqual(response.data['treino']['tipo'], 'longao')
        self.assertNotIn('Gel de carboidrato', response.data['resposta'])

        products_response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Quais produtos são indicados?'},
            format='json',
        )
        self.assertEqual(products_response.data['intent'], 'produtos')
        self.assertIn('Gel de carboidrato', products_response.data['resposta'])
        self.assertIn('(Suplemento)', products_response.data['resposta'])
        self.assertIn('Camisa', products_response.data['resposta'])
        self.assertIn('(Equipamento)', products_response.data['resposta'])
        self.assertIn('Qual desses produtos você gostaria', products_response.data['resposta'])
        self.assertNotIn('Produto privado', products_response.data['resposta'])
        self.assertNotIn('5:45 min/km', products_response.data['resposta'])
        self.assertEqual(
            products_response.data['opcoes'],
            [
                {'label': '🔍 Pesquisar preço da Camisa', 'payload': f'pesquisar_produto:{own_product.pk}'},
                {'label': '🔍 Pesquisar preço da Gel de carboidrato', 'payload': f'pesquisar_produto:{global_product.pk}'},
                {'label': '⬅️ Voltar ao Menu', 'payload': 'menu'},
            ],
        )

        warmup_during_product_choice = self.client.post(
            '/api/chatbot/',
            {
                'payload': 'alongamento',
                'context': products_response.data['context'],
            },
            format='json',
        )
        self.assertEqual(warmup_during_product_choice.data['intent'], 'alongamento')
        self.assertIn('alongamentos', warmup_during_product_choice.data['mensagem'])
        self.assertEqual(
            warmup_during_product_choice.data['context']['pending_product_ids'],
            [own_product.pk, global_product.pk],
        )

        price_search.return_value = [
            {
                'produto': 'Gel de carboidrato - loja A',
                'loja': 'loja-a.example',
                'preco': '8.90',
                'preco_formatado': 'R$ 8,90',
                'url': 'https://loja-a.example/gel',
            },
            {
                'produto': 'Gel de carboidrato - loja B',
                'loja': 'loja-b.example',
                'preco': '9.50',
                'preco_formatado': 'R$ 9,50',
                'url': 'https://loja-b.example/gel',
            },
            {
                'produto': 'Gel de carboidrato - loja C',
                'loja': 'loja-c.example',
                'preco': '10.00',
                'preco_formatado': 'R$ 10,00',
                'url': 'https://loja-c.example/gel',
            },
        ]
        prices_response = self.client.post(
            '/api/chatbot/',
            {
                'mensagem': '🔍 Pesquisar preço da Gel de carboidrato',
                'payload': f'pesquisar_produto:{global_product.pk}',
                'context': products_response.data['context'],
            },
            format='json',
        )
        self.assertEqual(prices_response.data['intent'], 'precos')
        self.assertEqual(len(prices_response.data['precos']), 3)
        self.assertEqual(prices_response.data['precos'][0]['loja'], 'loja-a.example')
        self.assertEqual(prices_response.data['precos'][0]['preco_formatado'], 'R$ 8,90')
        self.assertEqual(prices_response.data['precos'][0]['url'], 'https://loja-a.example/gel')
        self.assertIn('Deseja que eu também pesquise o preço de Camisa', prices_response.data['resposta'])
        self.assertEqual(prices_response.data['context']['pending_product_ids'], [own_product.pk])
        self.assertTrue(prices_response.data['resposta'].rstrip().endswith('?'))
        price_search.assert_called_once_with(global_product.nome_produto)

        second_quote = self.client.post(
            '/api/chatbot/',
            {
                'mensagem': '🔍 Pesquisar preço da Camisa',
                'payload': f'pesquisar_produto:{own_product.pk}',
                'context': prices_response.data['context'],
            },
            format='json',
        )
        self.assertEqual(second_quote.data['intent'], 'precos')
        self.assertEqual(len(second_quote.data['precos']), 3)
        self.assertIn('Quer conversar sobre aquecimento', second_quote.data['resposta'])
        self.assertEqual(price_search.call_count, 2)
        self.assertEqual(price_search.call_args.args, (own_product.nome_produto,))

    def test_chatbot_describes_future_workout_as_upcoming(self):
        self.client.force_authenticate(self.alice_user)
        self.workout.data = timezone.localdate() + timedelta(days=1)
        self.workout.distancia_km = '20.00'
        self.workout.save(update_fields=['data', 'distancia_km'])
        response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'iniciar', 'reset': True},
            format='json',
        )
        self.assertIn(
            f"próximo treino agendado para o dia {self.workout.data:%d/%m} é Longão de 20.00 km",
            response.data['mensagem'],
        )
        self.assertNotIn('treino prescrito para hoje', response.data['mensagem'])

    def test_chatbot_answers_warmup_and_post_workout_in_separate_steps(self):
        self.client.force_authenticate(self.alice_user)
        warmup_response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Quero o aquecimento'},
            format='json',
        )
        self.assertEqual(warmup_response.data['intent'], 'aquecimento')
        self.assertIn('mobilidade dinâmica', warmup_response.data['resposta'])
        self.assertTrue(warmup_response.data['resposta'].rstrip().endswith('?'))
        self.assertNotIn('5:45', warmup_response.data['resposta'])

        post_workout_response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Quero o alongamento pós-treino'},
            format='json',
        )
        self.assertEqual(post_workout_response.data['intent'], 'alongamento')
        self.assertIn('alongamentos', post_workout_response.data['resposta'])
        self.assertTrue(post_workout_response.data['resposta'].rstrip().endswith('?'))

    def test_chatbot_keeps_short_symptom_guidance(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Estou com cansaço no treino'},
            format='json',
        )
        self.assertEqual(response.data['intent'], 'cansaco')
        self.assertIn('pausa', response.data['resposta'])
        self.assertNotIn('Produtos indicados', response.data['resposta'])

    def test_chatbot_requires_product_choice_before_search_consent(self):
        self.client.force_authenticate(self.alice_user)
        Produto.objects.create(
            atleta=None,
            nome_produto='Gel de carboidrato',
            categoria='suplemento',
            preco='12.50',
        )
        Produto.objects.create(
            atleta=None,
            nome_produto='Bebida isotônica',
            categoria='nutricao',
            preco='8.00',
        )
        response = self.client.post('/api/chatbot/', {'mensagem': 'Quais suplementos temos?'}, format='json')
        self.assertEqual(response.data['intent'], 'produtos')
        self.assertEqual(response.data['context']['stage'], 'awaiting_product_selection')
        self.assertIn('Gel de carboidrato', response.data['resposta'])
        self.assertIn('Bebida isotônica', response.data['resposta'])
        self.assertIn('Qual desses produtos você gostaria', response.data['resposta'])
        self.assertTrue(response.data['resposta'].rstrip().endswith('?'))

    @patch('core.views.search_product_prices', side_effect=requests.RequestException('Busca indisponível'))
    def test_chatbot_uses_three_labeled_estimates_when_external_search_fails(self, search_prices):
        self.client.force_authenticate(self.alice_user)
        product = Produto.objects.create(
            atleta=None,
            nome_produto='Gel esportivo',
            categoria='suplemento',
            preco='20.00',
        )
        consent = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Quais suplementos temos?'},
            format='json',
        )
        response = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'pesquisa o Gel esportivo', 'context': consent.data['context']},
            format='json',
        )
        self.assertEqual(response.data['intent'], 'precos')
        self.assertEqual(len(response.data['precos']), 3)
        self.assertEqual({price['fonte'] for price in response.data['precos']}, {'estimativa'})
        self.assertTrue(all(price['url'].startswith('https://') for price in response.data['precos']))
        self.assertIn('estimativas simuladas', response.data['resposta'])
        self.assertTrue(response.data['resposta'].rstrip().endswith('?'))
        search_prices.assert_called_once_with(product.nome_produto)

    def test_chatbot_reset_returns_personalized_greeting_and_discards_old_context(self):
        self.client.force_authenticate(self.alice_user)
        product = Produto.objects.create(
            atleta=None,
            nome_produto='Gel de carboidrato',
            categoria='suplemento',
            preco='12.50',
        )
        product_prompt = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'Quero falar de suplemento'},
            format='json',
        )
        self.assertEqual(product_prompt.data['context']['stage'], 'awaiting_product_selection')
        self.assertEqual(product_prompt.data['context']['pending_product_ids'], [product.pk])

        reset = self.client.post(
            '/api/chatbot/',
            {
                'mensagem': 'iniciar',
                'reset': True,
                'context': product_prompt.data['context'],
            },
            format='json',
        )
        self.assertEqual(reset.data['intent'], 'saudacao')
        self.assertIn('Olá Alice!', reset.data['resposta'])
        self.assertIn('Seja bem-vindo ao RUN_LAB', reset.data['resposta'])
        self.assertIn('Longão', reset.data['resposta'])
        self.assertEqual(reset.data['context'], {})
        self.assertTrue(reset.data['resposta'].rstrip().endswith('?'))

        after_reset = self.client.post(
            '/api/chatbot/',
            {'mensagem': 'sim', 'context': reset.data['context']},
            format='json',
        )
        self.assertNotEqual(after_reset.data['intent'], 'precos')

    @patch('core.price_search.requests.get')
    def test_external_price_search_returns_three_lowest_results(self, get):
        get.return_value.text = '''
            <div class="result">
              <h2 class="result__title"><a href="https://store-a.example/item">Produto loja A</a></h2>
              <a class="result__url">https://store-a.example/item</a>
              <a class="result__snippet">Preço: R$ 22,90</a>
            </div>
            <div class="result">
              <h2 class="result__title"><a href="https://store-b.example/item">Produto loja B</a></h2>
              <a class="result__url">https://store-b.example/item</a>
              <a class="result__snippet">Preço: R$ 8,50</a>
            </div>
            <div class="result">
              <h2 class="result__title"><a href="https://store-c.example/item">Produto loja C</a></h2>
              <a class="result__url">https://store-c.example/item</a>
              <a class="result__snippet">Preço: R$ 13,99</a>
            </div>
            <div class="result">
              <h2 class="result__title"><a href="https://store-d.example/item">Produto loja D</a></h2>
              <a class="result__url">https://store-d.example/item</a>
              <a class="result__snippet">Preço: R$ 15,00</a>
            </div>
        '''
        results = search_product_prices('Gel de carboidrato')
        self.assertEqual([item['loja'] for item in results], [
            'store-b.example',
            'store-c.example',
            'store-d.example',
        ])
        self.assertEqual([item['preco_formatado'] for item in results], [
            'R$ 8,50',
            'R$ 13,99',
            'R$ 15,00',
        ])
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs['timeout'], 8)
        self.assertEqual(
            get.call_args.kwargs['params']['q'],
            '"Gel de carboidrato" preço comprar',
        )

    def test_chatbot_requires_authenticated_student_or_trainer(self):
        response = self.client.post('/api/chatbot/', {'mensagem': 'olá'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_chatbot_reports_when_student_has_no_assigned_profile(self):
        user_without_profile = User.objects.create_user(username='new-athlete', password='runner-pass-789')
        self.client.force_authenticate(user_without_profile)
        response = self.client.post('/api/chatbot/', {'mensagem': 'Qual meu treino?'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_root_serves_login_screen(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'login-form')
        self.assertContains(response, 'ENVIAR MEU TREINO ATUAL')
        self.assertContains(response, 'Como posso ajudar com sua corrida hoje?')
        self.assertNotContains(response, 'data-message=')

    @patch.dict(os.environ, {'GMAIL_EMAIL': 'coach@example.com', 'GMAIL_APP_PASSWORD': 'app-password'})
    @patch('core.rpa.smtplib.SMTP')
    def test_gmail_sender_uses_authenticated_tls_smtp(self, smtp_class):
        smtp = smtp_class.return_value.__enter__.return_value
        enviar_email_gmail('runner@example.com', 'Treino', 'Resumo')
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with('coach@example.com', 'app-password')
        smtp.send_message.assert_called_once()

    @patch('core.rpa.pyautogui.press')
    @patch('core.rpa.time.sleep')
    @patch('pywhatkit.sendwhatmsg_instantly')
    def test_whatsapp_sender_uses_international_number(self, send_message, sleep, press):
        enviar_whatsapp('5521999990000', 'Treino de hoje')
        send_message.assert_called_once_with(
            phone_no='+5521999990000',
            message='Treino de hoje',
            wait_time=12,
            tab_close=False,
        )
        sleep.assert_called_once_with(3)
        press.assert_called_once_with('enter')

    def test_whatsapp_sender_rejects_invalid_phone_number(self):
        with self.assertRaises(ValueError):
            enviar_whatsapp('invalid-number', 'Treino')
