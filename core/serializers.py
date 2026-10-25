from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .models import Atleta, Equipe, NotificacaoLog, Produto, Treino


class TreinoSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tipo_descanso_display = serializers.CharField(source='get_tipo_descanso_display', read_only=True)

    class Meta:
        model = Treino
        fields = (
            'id',
            'atleta',
            'data',
            'tipo',
            'tipo_display',
            'distancia_km',
            'pace_alvo',
            'repeticoes_tiro',
            'pace_tiro',
            'tipo_descanso',
            'tipo_descanso_display',
            'pace_descanso',
            'observacoes',
            'status',
            'status_display',
        )


class AtletaSerializer(serializers.ModelSerializer):
    treinos = TreinoSerializer(many=True, read_only=True)
    nivel_display = serializers.CharField(source='get_nivel_display', read_only=True)
    login = serializers.CharField(source='usuario.username', read_only=True)
    username = serializers.CharField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    def validate_username(self, value):
        User = get_user_model()
        users = User.objects.filter(username=value)
        if self.instance and self.instance.usuario_id:
            users = users.exclude(pk=self.instance.usuario_id)
        if users.exists():
            raise serializers.ValidationError('Este login já está em uso.')
        return value

    class Meta:
        model = Atleta
        fields = (
            'id', 'nome', 'email', 'whatsapp', 'nivel', 'nivel_display',
            'objetivo', 'criado_em', 'treinos', 'login', 'username', 'password',
        )
        read_only_fields = ('id', 'criado_em')

    def validate(self, attrs):
        if self.instance is None:
            if not attrs.get('username'):
                raise serializers.ValidationError({'username': 'Informe o login do aluno.'})
            if not attrs.get('password'):
                raise serializers.ValidationError({'password': 'Informe a senha inicial do aluno.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        username = validated_data.pop('username')
        password = validated_data.pop('password')
        User = get_user_model()
        usuario = User.objects.create_user(username=username, password=password, email=validated_data['email'])
        return Atleta.objects.create(usuario=usuario, **validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        username = validated_data.pop('username', None)
        password = validated_data.pop('password', None)
        email_changed = 'email' in validated_data
        instance = super().update(instance, validated_data)
        if not instance.usuario_id and (username or password):
            if not username or not password:
                raise serializers.ValidationError(
                    'Informe username e password para criar o acesso deste aluno.'
                )
            User = get_user_model()
            instance.usuario = User.objects.create_user(
                username=username,
                password=password,
                email=instance.email,
            )
            instance.save(update_fields=['usuario'])
        elif instance.usuario_id and (username or password or email_changed):
            update_fields = []
            if username:
                instance.usuario.username = username
                update_fields.append('username')
            if password:
                instance.usuario.set_password(password)
                update_fields.append('password')
            if email_changed:
                instance.usuario.email = instance.email
                update_fields.append('email')
            instance.usuario.save(update_fields=update_fields)
        return instance


class EquipeSerializer(serializers.ModelSerializer):
    atletas = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Atleta.objects.all(),
        required=False,
    )
    atletas_detalhes = AtletaSerializer(source='atletas', many=True, read_only=True)

    class Meta:
        model = Equipe
        fields = ('id', 'nome', 'descricao', 'atletas', 'atletas_detalhes')
        read_only_fields = ('id',)


class ProdutoSerializer(serializers.ModelSerializer):
    categoria_display = serializers.CharField(source='get_categoria_display', read_only=True)

    class Meta:
        model = Produto
        fields = '__all__'


class NotificacaoLogSerializer(serializers.ModelSerializer):
    canal_display = serializers.CharField(source='get_canal_display', read_only=True)
    status_display = serializers.CharField(source='get_status_envio_display', read_only=True)

    class Meta:
        model = NotificacaoLog
        fields = '__all__'
        read_only_fields = ('id', 'data_envio')


class ChatbotSerializer(serializers.Serializer):
    mensagem = serializers.CharField(max_length=1000, trim_whitespace=True)
