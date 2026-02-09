# 部署指南

## 生产环境部署步骤

### 1. 服务器准备

确保服务器已安装：
- Python 3.8+
- MySQL 8.0+
- Nginx (可选，用于反向代理)

### 2. 克隆项目

```bash
git clone <repository-url>
cd warehouse_manage_web
```

### 3. 创建虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

### 4. 安装依赖

```bash
pip install -r requirements.txt
```

### 5. 配置数据库

编辑 `warehouse_manage/settings.py`：

```python
# 生产环境配置
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com', 'your-server-ip']

# 使用MySQL
USE_MYSQL = True
```

设置环境变量：
```bash
export DB_NAME=warehouse_manage
export DB_USER=your_mysql_user
export DB_PASSWORD=your_mysql_password
export DB_HOST=localhost
export DB_PORT=3306
```

### 6. 创建数据库

```bash
mysql -u root -p
CREATE DATABASE warehouse_manage DEFAULT CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON warehouse_manage.* TO 'your_mysql_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 7. 运行迁移

```bash
python manage.py migrate
```

### 8. 初始化数据

```bash
python manage.py init_data
```

### 9. 收集静态文件

```bash
python manage.py collectstatic
```

### 10. 使用Gunicorn运行（推荐）

安装Gunicorn：
```bash
pip install gunicorn
```

运行：
```bash
gunicorn warehouse_manage.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

### 11. 配置Nginx（可选）

创建 `/etc/nginx/sites-available/warehouse_manage`：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /static/ {
        alias /path/to/warehouse_manage_web/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

启用站点：
```bash
sudo ln -s /etc/nginx/sites-available/warehouse_manage /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 12. 使用Supervisor管理进程（可选）

创建 `/etc/supervisor/conf.d/warehouse_manage.conf`：

```ini
[program:warehouse_manage]
command=/path/to/venv/bin/gunicorn warehouse_manage.wsgi:application --bind 0.0.0.0:8000 --workers 4
directory=/path/to/warehouse_manage_web
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/warehouse_manage/err.log
stdout_logfile=/var/log/warehouse_manage/out.log
```

启动：
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start warehouse_manage
```

## 安全建议

1. **修改SECRET_KEY**
   - 在生产环境使用新的随机SECRET_KEY
   - 不要将SECRET_KEY提交到版本控制

2. **禁用DEBUG模式**
   ```python
   DEBUG = False
   ```

3. **配置ALLOWED_HOSTS**
   ```python
   ALLOWED_HOSTS = ['your-domain.com', 'www.your-domain.com']
   ```

4. **使用HTTPS**
   - 配置SSL证书
   - 启用SECURE_SSL_REDIRECT

5. **定期备份数据库**
   ```bash
   mysqldump -u user -p warehouse_manage > backup.sql
   ```

6. **修改默认管理员密码**
   ```bash
   python manage.py shell
   >>> from system.models import User
   >>> user = User.objects.get(username='admin')
   >>> user.set_password('new_strong_password')
   >>> user.save()
   ```

## 性能优化

1. **启用数据库连接池**
2. **配置Redis缓存**
3. **优化静态文件服务**
4. **启用Gzip压缩**
5. **配置CDN**

## 监控

建议安装以下工具进行监控：
- Sentry（错误追踪）
- Prometheus + Grafana（性能监控）
- ELK Stack（日志分析）

## 备份策略

1. **数据库备份**：每天自动备份
2. **代码备份**：使用Git版本控制
3. **配置文件备份**：定期备份配置文件

## 故障排除

### 数据库连接失败
- 检查MySQL服务是否运行
- 检查数据库配置是否正确
- 检查防火墙设置

### 静态文件404
- 运行 `python manage.py collectstatic`
- 检查Nginx配置

### Session问题
- 检查数据库session表
- 清除浏览器Cookie

## 联系支持

如有问题，请联系技术支持团队。
