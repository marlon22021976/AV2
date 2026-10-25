import logging
from concurrent.futures import Future, ThreadPoolExecutor
from decimal import Decimal

import requests
from django.db import close_old_connections
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Atleta, Equipe, NotificacaoLog, Produto, Treino
from .price_search import mock_product_prices, search_product_prices
from .rpa import enviar_email_gmail, enviar_whatsapp
from .serializers import (
    AtletaSerializer,
    ChatbotSerializer,
    EquipeSerializer,
    NotificacaoLogSerializer,
    ProdutoSerializer,
    TreinoSerializer,
)

logger = logging.getLogger(__name__)
rpa_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='runlab-rpa')


def dashboard(request):
    return render(request, 'core/index.html')


def _format_workout_notification(atleta, treino, equipe=None):
    formatted_date = treino.data.strftime('%d/%m/%Y')
    workout_type = treino.get_tipo_display()
    subject = f'RUN_LAB - Prescrição de Treino: {workout_type} ({formatted_date})'
    if equipe is None:
        lines = [
            f'Olá, {atleta.nome}!',
            '',
            f'Segue a sua prescrição de treino para o dia {formatted_date}:',
        ]
    else:
        lines = [
            f'Olá, {atleta.nome}!',
            '',
            (
                f'Você tem uma nova prescrição de treino para a Equipe: {equipe.nome} '
                f'agendada para o dia {formatted_date}:'
            ),
        ]
    lines.extend([
        '',
        f'- Tipo: {workout_type}',
        f'- Distância Total: {Decimal(treino.distancia_km):.2f} km',
        f'- Pace Alvo Geral: {treino.pace_alvo}',
    ])

    if treino.tipo == 'intervalado':
        interval_lines = []
        if treino.repeticoes_tiro:
            interval_lines.append(f'- Repetições: {treino.repeticoes_tiro}')
        if treino.pace_tiro:
            interval_lines.append(f'- Ritmo do Tiro: {treino.pace_tiro}')
        if treino.tipo_descanso:
            interval_lines.append(f'- Tipo de Descanso: {treino.get_tipo_descanso_display()}')
        if treino.pace_descanso:
            interval_lines.append(f'- Pace / Tempo de Descanso: {treino.pace_descanso}')
        if interval_lines:
            lines.extend([
                '',
                'Detalhamento das Séries / Tiros:',
                *interval_lines,
            ])

    if treino.observacoes and treino.observacoes.strip():
        lines.extend(['', f'Observações do Coach: {treino.observacoes.strip()}'])
    return subject, '\n'.join(lines)


def _dispatch_notifications(atleta_id, email, whatsapp, subject, summary):
    _dispatch_notification_channel(atleta_id, email, whatsapp, 'email', subject, summary)
    _dispatch_notification_channel(atleta_id, email, whatsapp, 'whatsapp', subject, summary)


def _dispatch_notification_channel(atleta_id, email, whatsapp, canal, subject, summary):
    close_old_connections()
    try:
        if canal == 'email':
            enviar_email_gmail(email, subject, summary)
        else:
            enviar_whatsapp(whatsapp, summary)
        NotificacaoLog.objects.create(
            atleta_id=atleta_id,
            canal=canal,
            status_envio='sucesso',
            mensagem=summary,
        )
    except Exception as exc:
        logger.exception('Falha ao enviar notificação %s para o atleta %s.', canal, atleta_id)
        NotificacaoLog.objects.create(
            atleta_id=atleta_id,
            canal=canal,
            status_envio='falha',
            mensagem=f'{summary}\nErro: {exc}',
        )
    finally:
        close_old_connections()


def _report_rpa_worker_error(future: Future):
    if future.cancelled():
        logger.error('O job de RPA foi cancelado antes da execução.')
        return
    error = future.exception()
    if error:
        logger.error(
            'Falha inesperada no worker de RPA.',
            exc_info=(type(error), error, error.__traceback__),
        )


class StaffOrOwnerReadOnly(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        if getattr(view, 'action', None) == 'concluir':
            athlete = getattr(obj, 'atleta', obj)
            return getattr(athlete, 'usuario_id', None) == request.user.id
        return False


class TokenLoginView(ObtainAuthToken):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'is_staff': user.is_staff})


class TokenLogoutView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AtletaViewSet(viewsets.ModelViewSet):
    serializer_class = AtletaSerializer
    permission_classes = [StaffOrOwnerReadOnly]

    def get_queryset(self):
        queryset = Atleta.objects.select_related('usuario').prefetch_related('treinos')
        if self.request.user.is_staff:
            return queryset.all()
        return queryset.filter(usuario=self.request.user)

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Somente o personal pode cadastrar alunos.')
        serializer.save()

    def perform_destroy(self, instance):
        user = instance.usuario
        if user:
            user.delete()
        instance.delete()


class EquipeViewSet(viewsets.ModelViewSet):
    serializer_class = EquipeSerializer
    permission_classes = [StaffOrOwnerReadOnly]

    def get_queryset(self):
        queryset = Equipe.objects.prefetch_related('atletas')
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(atletas__usuario=self.request.user).distinct()

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Somente o personal pode cadastrar equipes.')
        serializer.save()


class TreinoViewSet(viewsets.ModelViewSet):
    serializer_class = TreinoSerializer
    permission_classes = [StaffOrOwnerReadOnly]

    def get_queryset(self):
        queryset = Treino.objects.select_related('atleta')
        if not self.request.user.is_staff:
            queryset = queryset.filter(atleta__usuario=self.request.user)
        atleta_id = self.request.query_params.get('atleta')
        if self.request.user.is_staff and atleta_id:
            queryset = queryset.filter(atleta_id=atleta_id)
        tipo = self.request.query_params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo=tipo)
        treino_status = self.request.query_params.get('status')
        if treino_status:
            queryset = queryset.filter(status=treino_status)
        return queryset.order_by('data', 'pk')

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Somente o personal pode cadastrar treinos.')
        serializer.save()

    @action(detail=True, methods=['post'], url_path='concluir')
    def concluir(self, request, pk=None):
        treino = self.get_object()
        if not request.user.is_staff and treino.atleta.usuario_id != request.user.id:
            raise PermissionDenied('Você só pode atualizar seus próprios treinos.')
        treino.status = 'concluido'
        treino.save(update_fields=['status'])
        return Response(self.get_serializer(treino).data)

    def _send_latest_workout(self, request):
        if request.user.is_staff:
            atleta_id = request.data.get('atleta_id')
            if not atleta_id:
                return Response(
                    {'detail': 'Informe atleta_id para selecionar o aluno.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                atleta = Atleta.objects.get(pk=atleta_id)
            except Atleta.DoesNotExist:
                return Response({'detail': 'Aluno não encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            try:
                atleta = Atleta.objects.get(usuario=request.user)
            except Atleta.DoesNotExist:
                return Response({'detail': 'O usuário não possui perfil de aluno.'}, status=status.HTTP_404_NOT_FOUND)

        treino = atleta.treinos.order_by('-data', '-pk').first()
        if treino is None:
            return Response({'detail': 'Nenhum treino foi cadastrado para este aluno.'}, status=status.HTTP_404_NOT_FOUND)

        subject, resumo = _format_workout_notification(atleta, treino)
        future = rpa_executor.submit(
            _dispatch_notifications,
            atleta.pk,
            atleta.email,
            atleta.whatsapp,
            subject,
            resumo,
        )
        future.add_done_callback(_report_rpa_worker_error)
        return Response(
            {
                'treino_id': treino.pk,
                'status': 'enfileirado',
                'detail': 'Os envios foram iniciados em segundo plano. Consulte /api/notificacoes/ para acompanhar os resultados.',
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'], url_path='enviar-ultimo')
    def enviar_ultimo(self, request):
        return self._send_latest_workout(request)

    @action(detail=False, methods=['post'], url_path='enviar-rpa')
    def enviar_rpa(self, request):
        return self._send_latest_workout(request)


class ProdutoViewSet(viewsets.ModelViewSet):
    serializer_class = ProdutoSerializer
    permission_classes = [StaffOrOwnerReadOnly]

    def get_queryset(self):
        if self.request.user.is_staff:
            queryset = Produto.objects.select_related('atleta').all()
        else:
            athlete = Atleta.objects.filter(usuario=self.request.user).first()
            if athlete is None:
                return Produto.objects.none()
            queryset = Produto.objects.filter(
                Q(atleta=athlete) | Q(atleta__isnull=True),
                ativo=True,
            ).select_related('atleta')
        atleta_id = self.request.query_params.get('atleta')
        if self.request.user.is_staff and atleta_id:
            queryset = queryset.filter(Q(atleta__isnull=True) | Q(atleta_id=atleta_id))
        return queryset

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Somente o personal pode cadastrar produtos.')
        serializer.save()


class NotificacaoLogViewSet(viewsets.ModelViewSet):
    serializer_class = NotificacaoLogSerializer
    permission_classes = [StaffOrOwnerReadOnly]

    def get_queryset(self):
        queryset = NotificacaoLog.objects.select_related('atleta')
        if not self.request.user.is_staff:
            queryset = queryset.filter(atleta__usuario=self.request.user)
        return queryset

    def perform_create(self, serializer):
        if not self.request.user.is_staff:
            raise PermissionDenied('Somente o personal pode criar notificações manualmente.')
        serializer.save()


def _is_yes(message):
    answer = message.strip(' .!?')
    return answer in {
        'sim', 's', 'claro', 'pode', 'pode ser', 'quero', 'quero sim', 'isso',
        'por favor', 'pesquise', 'pesquisar', 'pode pesquisar',
    } or answer.startswith(('sim ', 'sim,', 'claro ', 'pode ', 'quero sim'))


def _is_no(message):
    answer = message.strip(' .!?')
    return answer in {
        'não', 'nao', 'n', 'agora não', 'agora nao', 'não quero', 'nao quero',
    } or answer.startswith(('não ', 'nao '))


def _question(reply, question):
    return f'{reply.rstrip()}\n\n{question}'


def _build_chat_response(intent, reply, context, opcoes=None, **data):
    return Response({
        'intent': intent,
        'mensagem': reply,
        'resposta': reply,
        'context': context,
        'opcoes': opcoes if opcoes is not None else [],
        **data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def chatbot(request):
    incoming_message = request.data.get('payload') or request.data.get('mensagem')
    serializer = ChatbotSerializer(data={'mensagem': incoming_message})
    serializer.is_valid(raise_exception=True)
    message = serializer.validated_data['mensagem'].casefold().strip()

    if request.user.is_staff:
        athlete_id = request.data.get('atleta_id')
        athletes = Atleta.objects.all()
        if athlete_id:
            athletes = athletes.filter(pk=athlete_id)
        athlete = athletes.order_by('pk').first()
    else:
        athlete = Atleta.objects.filter(usuario=request.user).first()
    if athlete is None:
        return Response(
            {'detail': 'Não há aluno associado a este acesso.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    session_key = f'runlab_chat_{request.user.pk}_{athlete.pk}'
    reset_chat = request.data.get('reset') is True
    incoming_context = None if reset_chat else request.data.get('context')
    conversation = incoming_context if isinstance(incoming_context, dict) else request.session.get(session_key, {})
    conversation = {
        'stage': conversation.get('stage', ''),
        'product_id': conversation.get('product_id'),
        'pending_product_ids': conversation.get('pending_product_ids', []),
    }

    def set_conversation(stage='', product_id=None, pending_product_ids=None):
        nonlocal conversation
        conversation = (
            {
                'stage': stage,
                'product_id': product_id,
                'pending_product_ids': pending_product_ids or [],
            }
            if stage
            else {}
        )
        request.session[session_key] = conversation
    if reset_chat:
        set_conversation()
    today = timezone.localdate()
    treino = (
        Treino.objects.filter(atleta=athlete, status='pendente', data__gte=today)
        .order_by('data', 'pk')
        .first()
    )
    if treino is None:
        treino = (
            Treino.objects.filter(atleta=athlete, status='pendente')
            .order_by('-data', '-pk')
            .first()
        )

    produtos = list(
        Produto.objects.filter(ativo=True)
        .filter(Q(atleta__isnull=True) | Q(atleta=athlete))
        .select_related('atleta')
        .order_by('-pk')
    )
    aquecimento_por_tipo = {
        'regenerativo': 'Faça 5–10 minutos de caminhada e trote leve, com mobilidade dinâmica confortável.',
        'continuo': 'Faça 8–12 minutos de trote leve e mobilidade dinâmica; aumente o ritmo gradualmente.',
        'intervalado': 'Faça 10–15 minutos de trote leve, mobilidade dinâmica e 3–4 acelerações progressivas antes dos tiros.',
        'longao': 'Faça 8–12 minutos de caminhada/trote leve e mobilidade dinâmica, começando o longão em ritmo fácil.',
    }
    pos_treino_por_tipo = {
        'regenerativo': 'Finalize com alguns minutos caminhando e alongamentos leves, sem forçar a amplitude.',
        'continuo': 'Desaqueça caminhando por 5 minutos e faça alongamentos suaves, sem dor.',
        'intervalado': 'Caminhe ou trote bem leve por 5–10 minutos; alongue suavemente após a respiração normalizar.',
        'longao': 'Hidrate-se, caminhe alguns minutos e faça alongamentos confortáveis, sem insistir em áreas doloridas.',
    }
    if treino:
        descanso_label = treino.get_tipo_descanso_display()
        treino_data = {
            'id': treino.pk,
            'data': treino.data.isoformat(),
            'tipo': treino.tipo,
            'tipo_display': treino.get_tipo_display(),
            'distancia_km': str(treino.distancia_km),
            'pace_alvo': treino.pace_alvo,
            'repeticoes_tiro': treino.repeticoes_tiro or '',
            'pace_tiro': treino.pace_tiro or '',
            'tipo_descanso': treino.tipo_descanso or '',
            'tipo_descanso_display': descanso_label or '',
            'pace_descanso': treino.pace_descanso or '',
            'observacoes': treino.observacoes or '',
            'status': treino.status,
        }
        aquecimento = aquecimento_por_tipo[treino.tipo]
        pos_treino = pos_treino_por_tipo[treino.tipo]
    else:
        treino_data = None
        aquecimento = 'Sem treino pendente cadastrado. Se fizer uma atividade leve, comece com caminhada/trote e mobilidade dinâmica.'
        pos_treino = 'Após qualquer atividade, desacelere gradualmente; alongue apenas de forma confortável e sem dor.'

    def interval_details():
        if not treino or treino.tipo != 'intervalado':
            return ''
        details = []
        if treino.repeticoes_tiro:
            details.append(f'- Repetições: {treino.repeticoes_tiro}')
        if treino.pace_tiro:
            details.append(f'- Ritmo do tiro: {treino.pace_tiro}')
        if treino.tipo_descanso:
            descanso = f'- Descanso: {treino.get_tipo_descanso_display()}'
            if treino.pace_descanso:
                descanso += f' — {treino.pace_descanso}'
            details.append(descanso)
        if treino.pace_descanso:
            if not treino.tipo_descanso:
                details.append(f'- Ritmo/tempo de descanso: {treino.pace_descanso}')
        return '\n\nDetalhamento dos tiros:\n' + '\n'.join(details) if details else ''

    produtos_data = [
        {
            'id': produto.pk,
            'nome_produto': produto.nome_produto,
            'categoria': produto.get_categoria_display(),
            'preco': str(produto.preco),
            'descricao': produto.descricao,
            'global': produto.atleta_id is None,
        }
        for produto in produtos
    ]
    first_name = athlete.nome.split()[0]
    if treino:
        if treino.data == today:
            workout_intro = 'O seu treino prescrito para hoje'
            workout_date_label = 'hoje'
        elif treino.data > today:
            workout_intro = f"O seu próximo treino agendado para o dia {treino.data:%d/%m}"
            workout_date_label = f"agendado para o dia {treino.data:%d/%m}"
        else:
            workout_intro = f"O seu treino pendente desde o dia {treino.data:%d/%m}"
            workout_date_label = f"pendente desde o dia {treino.data:%d/%m}"
    else:
        workout_intro = ''
        workout_date_label = ''

    product_by_id = {product.pk: product for product in produtos}

    def contextual_options():
        pending = conversation.get('pending_product_ids', [])
        if conversation.get('stage') in ('awaiting_product_selection', 'awaiting_next_product'):
            options = [
                {
                    'label': f'🔍 Pesquisar preço da {product_by_id[product_id].nome_produto}',
                    'payload': f'pesquisar_produto:{product_id}',
                }
                for product_id in pending
                if product_id in product_by_id
            ]
            options.append({'label': '⬅️ Voltar ao Menu', 'payload': 'menu'})
            return options
        if treino and treino.tipo == 'intervalado':
            return [
                {'label': '🔥 Dica de Aquecimento', 'payload': 'aquecimento'},
                {'label': '🧘 Alongamento Pós-Treino', 'payload': 'alongamento'},
                {'label': '🛍️ Produtos Cadastrados', 'payload': 'listar_produtos'},
            ]
        return [
            {'label': '🔥 Aquecimento', 'payload': 'aquecimento'},
            {'label': '🧘 Alongamento', 'payload': 'alongamento'},
            {'label': '💧 Hidratação', 'payload': 'hidratacao'},
            {'label': '🛍️ Ver Produtos Cadastrados', 'payload': 'listar_produtos'},
        ]

    def _chat_response(intent, reply, context, **data):
        return _build_chat_response(
            intent,
            reply,
            context,
            opcoes=contextual_options(),
            **data,
        )

    def product_name_matches(product):
        product_name = product.nome_produto.casefold()
        if product_name in message:
            return True
        searchable_terms = [
            term for term in product_name.split()
            if term not in {'de', 'da', 'do', 'das', 'dos', 'para', 'com'}
        ]
        return bool(searchable_terms) and all(term in message for term in searchable_terms)

    def matches_from(ids):
        available = [
            product_by_id[product_id]
            for product_id in ids
            if product_id in product_by_id
        ]
        return sorted(
            (
                product for product in available
                if product_name_matches(product)
            ),
            key=lambda product: len(product.nome_produto),
            reverse=True,
        )

    def ask_for_product_choice(product_ids):
        choices = [product_by_id[product_id] for product_id in product_ids if product_id in product_by_id]
        if not choices:
            set_conversation()
            return _question(
                'Não há outros produtos ativos recomendados para pesquisar.',
                'Quer conversar sobre aquecimento ou recuperação?',
            )
        if len(choices) == 1:
            prompt = f"Deseja que eu também pesquise o preço de {choices[0].nome_produto}?"
        else:
            names = ', '.join(product.nome_produto for product in choices)
            prompt = f"Também posso pesquisar {names}. Qual desses produtos você quer consultar?"
        set_conversation('awaiting_next_product', pending_product_ids=product_ids)
        return prompt

    def search_selected_product(selected, remaining_ids):
        fallback = False
        try:
            prices = search_product_prices(selected.nome_produto)
        except (requests.RequestException, ValueError) as exc:
            logger.warning('Busca externa de preços falhou para o produto %s: %s', selected.pk, exc)
            prices = []
        if len(prices) < 3:
            fallback = True
            prices = mock_product_prices(selected.nome_produto, selected.preco)

        if fallback:
            reply = (
                f"Não consegui confirmar três ofertas atuais para {selected.nome_produto}. "
                "Seguem estimativas simuladas baseadas no preço cadastrado "
                '(não são ofertas confirmadas).'
            )
        else:
            reply = f"Encontrei três ofertas para {selected.nome_produto}."
        follow_up = ask_for_product_choice(remaining_ids)
        return _question(reply, follow_up), prices

    def list_recommended_products():
        if not produtos:
            set_conversation()
            return _chat_response(
                'produtos',
                _question(
                    'Não há produtos ativos recomendados para você no momento.',
                    'Quer uma dica de aquecimento para o treino?',
                ),
                conversation,
                produtos=[],
            )

        product_summary = ' e '.join(
            f"**{product.nome_produto}** ({product.get_categoria_display()})"
            for product in produtos
        )
        product_ids = [product.pk for product in produtos]
        set_conversation('awaiting_product_selection', pending_product_ids=product_ids)
        prompt = (
            f"Atualmente, seu personal deixou recomendado para você: {product_summary}. "
            "Qual desses produtos você gostaria que eu pesquisasse na internet para te mostrar "
            "as 3 opções mais baratas do mercado com link para compra?"
        )
        return _chat_response('produtos', prompt, conversation, produtos=produtos_data)

    conversation_stage = conversation.get('stage')
    pending_product_ids = [
        product_id
        for product_id in conversation.get('pending_product_ids', [])
        if product_id in product_by_id
    ]

    if message in ('menu', 'voltar ao menu'):
        set_conversation()
        reply = _question('Claro, voltamos ao menu do Coach IA.', 'O que você gostaria de consultar agora?')
        return _chat_response('menu', reply, conversation)

    if message.startswith('pesquisar_produto:'):
        try:
            requested_id = int(message.partition(':')[2])
        except ValueError:
            requested_id = None
        selected = product_by_id.get(requested_id)
        if selected and requested_id in pending_product_ids:
            remaining_ids = [product_id for product_id in pending_product_ids if product_id != requested_id]
            reply, prices = search_selected_product(selected, remaining_ids)
            return _chat_response('precos', reply, conversation, precos=prices)
        set_conversation()
        reply = _question(
            'Esse produto não está mais disponível para consulta.',
            'Quer ver novamente os produtos cadastrados?',
        )
        return _chat_response('produtos', reply, conversation)

    # Training topics take precedence over product-selection state.
    if any(word in message for word in ('aquecimento', 'pré-treino', 'pre-treino', 'aquecer')):
        reply = _question(
            aquecimento,
            'Quer que eu mostre os alongamentos, passe orientações de hidratação ou consulte os produtos recomendados?',
        )
        return _chat_response('aquecimento', reply, conversation, sugestao=aquecimento)

    if any(word in message for word in ('alongamento', 'alongar', 'pós-treino', 'pos-treino', 'desaquece')):
        reply = _question(
            pos_treino,
            'Quer que eu mostre as dicas de aquecimento, passe orientações de hidratação ou consulte os produtos recomendados?',
        )
        return _chat_response('alongamento', reply, conversation, sugestao=pos_treino)

    if any(word in message for word in ('recuperação', 'recuperacao', 'recuperar', 'descanso')):
        reply = _question(
            'Priorize uma recuperação gradual: hidratação, alimentação adequada e descanso; retome a intensidade conforme se sentir bem.',
            'Quer orientações de alongamento, aquecimento ou consultar os produtos recomendados?',
        )
        return _chat_response('recuperacao', reply, conversation)

    if any(word in message for word in ('hidratação', 'hidratacao', 'hidratar', 'água', 'agua')):
        reply = _question(
            'Beba água regularmente e ajuste a hidratação ao calor, à duração do treino e à sua sede.',
            'Quer consultar o aquecimento, os alongamentos ou os produtos recomendados?',
        )
        return _chat_response('hidratacao', reply, conversation)

    if any(word in message for word in ('alimentação', 'alimentacao', 'comida', 'nutrição', 'nutricao')):
        reply = _question(
            'Prefira uma alimentação adequada ao horário e à duração do treino; para um plano individual, consulte um nutricionista esportivo.',
            'Quer consultar orientações de hidratação, recuperação ou produtos recomendados?',
        )
        return _chat_response('alimentacao', reply, conversation)

    if any(word in message for word in ('pace', 'ritmo', 'velocidade')):
        reply = _question(
            'Ajuste o ritmo ao esforço prescrito e à sua sensação; não acelere se houver dor ou mal-estar.',
            'Quer orientações de aquecimento, recuperação ou consultar os produtos recomendados?',
        )
        return _chat_response('ritmo', reply, conversation)

    if any(word in message for word in ('dor', 'machuc', 'lesão', 'lesao')):
        reply = _question(
            'Se sentir dor aguda ou persistente, interrompa o treino e procure um profissional de saúde.',
            'A dor é aguda ou piora quando você corre? Posso também ajudar com recuperação.',
        )
        return _chat_response('dor', reply, conversation)

    if any(word in message for word in ('cansaço', 'cansaco', 'fadiga', 'exaust')):
        reply = _question(
            'Se estiver muito cansado, reduza o ritmo ou faça uma pausa; procure orientação se a fadiga persistir.',
            'Quer conversar sobre recuperação, aquecimento ou hidratação?',
        )
        return _chat_response('cansaco', reply, conversation)

    if conversation_stage in ('awaiting_product_selection', 'awaiting_next_product'):
        selected_products = matches_from(pending_product_ids)
        if not selected_products and conversation_stage == 'awaiting_product_selection':
            selected_products = matches_from(product_by_id.keys())
        if selected_products:
            selected = selected_products[0]
            remaining_ids = [product_id for product_id in pending_product_ids if product_id != selected.pk]
            reply, prices = search_selected_product(selected, remaining_ids)
            return _chat_response('precos', reply, conversation, precos=prices)
        if _is_no(message):
            set_conversation()
            reply = _question(
                'Tudo bem, não vou pesquisar outro produto.',
                'Quer conversar sobre aquecimento ou recuperação?',
            )
            return _chat_response('precos', reply, conversation)
        if _is_yes(message) and len(pending_product_ids) == 1:
            selected = product_by_id[pending_product_ids[0]]
            reply, prices = search_selected_product(selected, [])
            return _chat_response('precos', reply, conversation, precos=prices)
        if _is_yes(message) and len(pending_product_ids) > 1:
            names = ', '.join(product_by_id[product_id].nome_produto for product_id in pending_product_ids)
            reply = _question('Claro, posso pesquisar mais um.', f'Qual produto você escolhe: {names}?')
            set_conversation('awaiting_product_selection', pending_product_ids=pending_product_ids)
            return _chat_response('produtos', reply, conversation)
        choices = [product_by_id[product_id].nome_produto for product_id in pending_product_ids]
        names = ', '.join(choices)
        reply = _question(
            'Não identifiquei qual produto você escolheu.',
            f'Qual deles você quer pesquisar: {names}?',
        )
        return _chat_response('produtos', reply, conversation, produtos=produtos_data)

    product_match = next(
        (
            product for product in sorted(produtos, key=lambda item: len(item.nome_produto), reverse=True)
            if product_name_matches(product)
        ),
        None,
    )
    if product_match and any(
        term in message for term in ('pesquis', 'preço', 'preco', 'cot', 'valor', 'buscar', 'busca')
    ):
        all_product_ids = [product.pk for product in produtos]
        remaining_ids = [product_id for product_id in all_product_ids if product_id != product_match.pk]
        reply, prices = search_selected_product(product_match, remaining_ids)
        return _chat_response('precos', reply, conversation, precos=prices)

    if message in ('listar_produtos', 'produtos', 'suplementos', 'equipamentos') or any(
        word in message for word in ('produto', 'suplement', 'equipamento', 'preço', 'preco', 'cotação', 'cotacao')
    ):
        return list_recommended_products()
    if any(word in message for word in ('bem', 'ótimo', 'otimo', 'tranquilo', 'disposto')):
        reply = _question(
            'Que bom! Escute seu corpo e siga o esforço previsto para a sessão.',
            'Quer uma orientação de aquecimento ou prefere conversar sobre recuperação?',
        )
        return _chat_response('bem-estar', reply, conversation)

    if reset_chat:
        if treino_data:
            greeting = (
                f"Olá {first_name}! Seja bem-vindo ao RUN_LAB. {workout_intro} é "
                f"{treino_data['tipo_display']} de {treino_data['distancia_km']} km, "
                f"com pace alvo de {treino_data['pace_alvo']}."
                f"{interval_details()}"
            )
        else:
            greeting = (
                f"Olá {first_name}! Seja bem-vindo ao RUN_LAB. Você não tem treino prescrito para hoje."
            )
        reply = _question(greeting, 'Como você está se sentindo para a sessão de hoje?')
        return _chat_response('saudacao', reply, conversation, treino=treino_data)

    if treino_data:
        reply = (
            f"Olá {first_name}! Seu treino {workout_date_label} é {treino_data['tipo_display']} de "
            f"{treino_data['distancia_km']} km, com pace alvo de {treino_data['pace_alvo']}."
            f"{interval_details()}"
        )
    else:
        reply = f'Olá {first_name}! Você não tem treino pendente no momento.'
    reply = _question(reply, 'Como está se sentindo para essa sessão?')
    return _chat_response('treino', reply, conversation, treino=treino_data)
