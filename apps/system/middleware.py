from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """
    登录验证中间件
    确保用户在访问系统时已经登录，防止未登录访问接口
    """
    
    def __init__(self, get_response):
        """
        初始化中间件
        :param get_response: Django响应处理函数
        """
        self.get_response = get_response
        # 不需要登录验证的URL路径
        self.exempt_urls = [
            '/login/',
            '/logout/',
            '/captcha/',
            '/static/',
        ]

    def __call__(self, request):
        """
        处理请求
        :param request: HTTP请求对象
        :return: HTTP响应对象
        """
        # 检查请求路径是否在豁免列表中
        path = request.path
        if not any(path.startswith(url) for url in self.exempt_urls):
            # 检查用户是否已登录
            if 'user_id' not in request.session:
                # 未登录，重定向到登录页面
                return redirect(reverse('login'))
        
        response = self.get_response(request)
        return response
