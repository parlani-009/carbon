from django.urls import path
from . import views

urlpatterns = [
    path('trigger/', views.trigger_task, name='trigger_task'),
    path('chat/list/', views.list_chats, name='list_chats'),
    path('chat/make/', views.make_chat, name='make_chat'),
    path('chat/messages/', views.get_messages, name='get_messages'),
    path('chat/', views.chat, name='chat'),
]
