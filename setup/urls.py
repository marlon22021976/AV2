"""
URL configuration for setup project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import (
    AtletaViewSet,
    EquipeViewSet,
    NotificacaoLogViewSet,
    ProdutoViewSet,
    TokenLoginView,
    TokenLogoutView,
    TreinoViewSet,
    chatbot,
    dashboard,
)

router = DefaultRouter()
router.register('atletas', AtletaViewSet, basename='atleta')
router.register('equipes', EquipeViewSet, basename='equipe')
router.register('treinos', TreinoViewSet, basename='treino')
router.register('produtos', ProdutoViewSet, basename='produto')
router.register('notificacoes', NotificacaoLogViewSet, basename='notificacao')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/token/', TokenLoginView.as_view(), name='api-token-auth'),
    path('api/auth/logout/', TokenLogoutView.as_view(), name='api-token-logout'),
    path('api/chatbot/', chatbot, name='chatbot'),
    path('api/', include(router.urls)),
    path('', dashboard, name='dashboard'),
]
