"""
URL configuration for warehouse_manage project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
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
from django.urls import path
from system import views
from ac_data import views as ac_views

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 认证相关
    path('', views.login_view, name='login'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('captcha/', views.captcha_view, name='captcha'),
    
    # 首页
    path('index/', views.index_view, name='index'),
    
    # 用户管理
    path('system/user/list/', views.user_list_view, name='user_list'),
    path('system/user/add/', views.user_add_view, name='user_add'),
    path('system/user/edit/', views.user_edit_view, name='user_edit'),
    path('system/user/delete/', views.user_delete_view, name='user_delete'),
    path('system/user/form/', views.user_form_view, name='user_form'),  # 新增


    
    # 角色管理
    path('system/role/list/', views.role_list_view, name='role_list'),
    path('system/role/add/', views.role_add_view, name='role_add'),
    path('system/role/edit/', views.role_edit_view, name='role_edit'),
    path('system/role/delete/', views.role_delete_view, name='role_delete'),
    path('system/role/all/', views.get_all_roles_view, name='role_all'),
    path('system/role/form/', views.role_form_view, name='role_form'),

    # 菜单管理
    path('system/menu/list/', views.menu_list_view, name='menu_list'),
    path('system/menu/add/', views.menu_add_view, name='menu_add'),
    path('system/menu/edit/', views.menu_edit_view, name='menu_edit'),
    path('system/menu/delete/', views.menu_delete_view, name='menu_delete'),
    path('system/menu/tree/', views.menu_tree_view, name='menu_tree'),
    path('system/menu/parent-list/', views.menu_parent_list_view, name='menu_parent_list'),
    path('system/memu/form/', views.memu_form_view, name='menu_form'),

    # 任务管理
     path('system/core/down_report/', views.core_down_report, name='down_report'),
    
    # AC栅格数据管理
    path('ac/data/list/', ac_views.ac_list_page_view, name='ac_list_page'),
    path('ac/data/list/data/', ac_views.ac_list_view, name='ac_list'),
    path('ac/data/upload/', ac_views.ac_upload_view, name='ac_upload'),
    path('ac/data/edit/', ac_views.ac_edit_view, name='ac_edit'),
    path('ac/data/delete/', ac_views.ac_delete_view, name='ac_delete'),
]
