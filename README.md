# 地震滑坡灾害评估系统后

> landslide_evaluation

基于 Django + MySQL + LayUI + JavaScript + HTML + GDAL 的地震滑坡灾害评估系统后台

## 项目简介

这是一个企业级的地震滑坡灾害评估系统后台，实现了完整的用户权限管理体系和AC栅格数据管理功能，包括用户管理、角色管理、菜单管理、AC栅格文件管理等核心功能。系统采用现代化的Web技术栈，代码质量高，安全性强，易于扩展。

## 技术架构

### 后端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.x | 编程语言 |
| Django | 4.2.26 | Web框架，提供MVC架构、ORM、中间件等核心功能 |
| Pillow | 10.3.0 | 图像处理库，用于生成登录验证码 |
| GDAL | 3.6.2 | 地理空间数据抽象库，用于处理TIF文件和提取地理坐标 |
| SQLite | 3.x | 测试环境数据库 |
| MySQL | 8.0+ | 生产环境数据库 |

### 前端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| LayUI | 2.8.0 | 前端UI框架，提供表格、表单、弹窗等组件 |
| JavaScript | ES6 | 前端交互逻辑 |
| jQuery | 3.6.0 | DOM操作和AJAX请求 |
| HTML5 | - | 页面结构 |
| CSS3 | - | 页面样式 |

### 技术使用位置说明

#### Django框架
- **models.py**: 定义数据模型（User、Role、Menu、AcTif）
- **views.py**: 业务逻辑处理（登录、用户管理、角色管理、菜单管理、AC数据管理）
- **urls.py**: URL路由配置
- **middleware.py**: 登录验证中间件，保证系统安全
- **settings.py**: 项目配置（数据库、Session、静态文件、文件上传等）

#### Pillow
- **system/views.py - generate_captcha()**: 生成4位数字验证码图片，包含干扰线

#### GDAL
- **ac_data/views.py - ac_upload_view()**: 从上传的TIF文件中提取地理坐标范围（GeoTransform）
- **地理数据处理**: 读取栅格数据的经纬度边界信息

#### LayUI
- **templates/*.html**: 所有前端页面使用LayUI组件
- **表格组件**: 用户列表、角色列表、菜单列表、AC数据列表的数据展示
- **表单组件**: 用户、角色、菜单的添加和编辑表单
- **上传组件**: AC文件上传功能
- **树形组件**: 角色管理中的菜单权限树形选择
- **弹窗组件**: 新增/编辑功能的模态弹窗

#### MySQL/SQLite
- **SQLite**: 测试环境使用，无需额外配置
- **MySQL**: 生产环境使用，提供更好的性能和并发支持

## 功能模块

### 1. 登录功能 ✓
- [x] 用户名密码登录
- [x] Pillow生成的验证码验证
- [x] 30分钟Session超时（无操作自动退出）
- [x] 登录验证中间件（防止未登录访问）
- [x] 密码加密存储（Django PBKDF2算法）
- [x] 账号状态检查（停用账号无法登录）

### 2. 用户管理 ✓
- [x] 用户列表展示（分页、搜索）
- [x] 新增用户（昵称、部门、手机、用户名、密码、性别、状态、岗位、角色）
- [x] 编辑用户（支持修改所有信息，密码可选修改）
- [x] 删除用户
- [x] 部门三级选择（总部→研发→产研）
- [x] 用户名唯一性验证
- [x] 角色关联（多对多）

### 3. 角色管理 ✓
- [x] 角色列表展示（分页、搜索）
- [x] 新增角色（角色名称、角色英文名称、菜单权限）
- [x] 编辑角色
- [x] 删除角色
- [x] 角色名称唯一性验证
- [x] 树形菜单权限选择
- [x] 关键字搜索（LIKE模糊查询）

### 4. 菜单管理 ✓
- [x] 菜单列表展示（树形结构）
- [x] 新增菜单（最多二级）
- [x] 编辑菜单
- [x] 删除菜单（有子菜单时禁止删除）
- [x] 二级菜单限制（二级菜单不可再添加子菜单）
- [x] 关键字搜索（LIKE模糊查询）
- [x] 菜单排序

### 5. AC栅格数据管理 ✓
- [x] AC文件上传（支持.tif格式，最大200MB）
- [x] 使用GDAL自动提取地理坐标范围
- [x] AC数据列表展示（分页、搜索）
- [x] 多条件搜索（文件名称、本地名称、经纬度范围）
- [x] 编辑AC文件名称
- [x] 删除AC文件（同时删除服务器文件）
- [x] 自动生成本地文件名（id_经度_纬度格式）

### 6. 权限控制 ✓
- [x] 基于角色的菜单显示
- [x] 不同角色登录显示不同菜单
- [x] 登录验证中间件
- [x] Session管理

## 项目结构

```
warehouse_manage_web2/
├── warehouse_manage/          # Django项目配置
│   ├── settings.py           # 配置文件（数据库切换、Session、文件上传等）
│   ├── urls.py               # 总路由配置
│   ├── wsgi.py               # WSGI配置
│   └── asgi.py               # ASGI配置
├── apps/
│   ├── system/               # 系统管理模块
│   │   ├── models.py         # 数据模型（User、Role、Menu）
│   │   ├── views.py          # 视图函数（所有业务逻辑）
│   │   ├── middleware.py     # 登录验证中间件
│   │   ├── management/       # 管理命令
│   │   │   └── commands/
│   │   │       └── init_data.py  # 初始化测试数据命令
│   │   └── migrations/       # 数据库迁移文件
│   └── ac_data/              # AC栅格数据管理模块
│       ├── models.py         # 数据模型（AcTif）
│       ├── views.py          # 视图函数（AC数据CRUD、文件上传）
│       ├── apps.py           # 应用配置
│       └── migrations/       # 数据库迁移文件
├── templates/                # HTML模板
│   ├── login.html           # 登录页面
│   ├── index.html           # 主工作台页面
│   ├── system/              # 系统管理模块页面
│   │   ├── user_list.html   # 用户列表
│   │   ├── user_form.html   # 用户表单
│   │   ├── role_list.html   # 角色列表
│   │   ├── role_form.html   # 角色表单
│   │   ├── menu_list.html   # 菜单列表
│   │   └── menu_form.html   # 菜单表单
│   └── ac_data/             # AC数据管理模块页面
│       └── ac_list.html     # AC数据列表（含上传、编辑、删除）
├── static/                  # 静态资源
│   ├── layui/              # LayUI框架
│   ├── css/                # 自定义CSS
│   ├── js/                 # 自定义JS
│   └── images/             # 图片资源
├── data/                   # 数据存储目录
│   └── ac/                 # AC栅格文件存储目录
├── sql/
│   └── init.sql            # MySQL初始化SQL（包含ac_tif表）
├── requirements.txt        # Python依赖包（Django、Pillow、GDAL等）
├── manage.py              # Django管理脚本
└── README.md              # 项目说明文档
```

## 安装部署

### 环境要求

- Python 3.8+
- pip
- MySQL 8.0+（生产环境）

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd warehouse_manage_web
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **数据库配置**

#### 测试环境（使用SQLite）
无需配置，直接使用即可。

#### 生产环境（使用MySQL）

**方式一：使用环境变量**
```bash
export USE_MYSQL=True
export DB_NAME=warehouse_manage
export DB_USER=root
export DB_PASSWORD=your_password
export DB_HOST=localhost
export DB_PORT=3306
```

**方式二：修改settings.py**

编辑 `warehouse_manage/settings.py`，找到数据库配置部分：
```python
USE_MYSQL = True  # 改为True启用MySQL
```

然后配置MySQL连接参数：
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'warehouse_manage',
        'USER': 'root',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '3306',
        ...
    }
}
```

**创建MySQL数据库**
```bash
# 方式一：使用SQL文件
mysql -u root -p < sql/init.sql

# 方式二：手动创建数据库
mysql -u root -p
CREATE DATABASE warehouse_manage DEFAULT CHARACTER SET utf8mb4;
```

4. **运行数据库迁移**
```bash
python manage.py migrate
```

5. **初始化测试数据**
```bash
python manage.py init_data
```

6. **启动开发服务器**
```bash
python manage.py runserver
```

7. **访问系统**

打开浏览器访问：http://127.0.0.1:8000

## 测试账号

| 用户类型 | 用户名 | 密码 | 权限 |
|---------|--------|------|------|
| 系统管理员 | admin | admin888 | 所有菜单权限 |
| 普通用户 | test | test123 | 仅用户管理权限 |

## 数据库切换说明

本系统支持SQLite（测试环境）和MySQL（生产环境）两种数据库，可以通过以下方式自由切换：

### 方式一：环境变量（推荐）
```bash
# 使用SQLite（默认）
export USE_MYSQL=False

# 使用MySQL
export USE_MYSQL=True
export DB_NAME=warehouse_manage
export DB_USER=root
export DB_PASSWORD=your_password
export DB_HOST=localhost
export DB_PORT=3306
```

### 方式二：修改settings.py
编辑 `warehouse_manage/settings.py`：
```python
# 使用SQLite
USE_MYSQL = False

# 使用MySQL
USE_MYSQL = True
```

### 切换后的操作
1. 运行迁移：`python manage.py migrate`
2. 初始化数据：`python manage.py init_data`

## 安全特性

### 1. 登录安全
- 验证码防暴力破解
- Session超时机制（30分钟）
- 密码加密存储（PBKDF2算法）
- 账号状态检查

### 2. 访问控制
- 登录验证中间件，防止未登录访问
- 基于角色的菜单权限控制
- CSRF防护（Django内置）

### 3. 数据安全
- SQL注入防护（Django ORM）
- XSS防护（模板自动转义）
- 密码不明文存储

## 代码规范

### 1. 命名规范
- **包名**: 全小写，如 `system`
- **类名**: 大驼峰，如 `User`, `LoginRequiredMiddleware`
- **函数/方法名**: 小写+下划线，如 `login_view`, `get_menus`
- **变量名**: 小写+下划线，如 `user_id`, `menu_tree`
- **数据库字段**: 小写+下划线，如 `menu_name`, `created_at`
- **URL路径**: 小写+斜杠，如 `/system/user/list/`

### 2. 注释规范
- 所有函数/方法都有中文注释说明功能
- 函数参数和返回值都有说明
- 关键业务逻辑有中文注释
- 模型字段有 `verbose_name` 说明

### 3. 代码质量
- 遵循Django最佳实践
- 使用ORM而非原生SQL
- 统一的错误处理
- 统一的返回格式

## 开发说明

### 添加新菜单
1. 在系统中通过"菜单管理"添加菜单项
2. 创建对应的视图函数
3. 在 `urls.py` 中添加路由
4. 创建HTML模板
5. 为角色分配菜单权限

### 添加新角色
1. 在系统中通过"角色管理"添加角色
2. 选择该角色可见的菜单
3. 在"用户管理"中为用户分配角色

### 修改Session超时时间
编辑 `warehouse_manage/settings.py`：
```python
SESSION_COOKIE_AGE = 30 * 60  # 单位：秒，30分钟
```

## 常见问题

### 1. 忘记管理员密码
```bash
python manage.py shell
>>> from system.models import User
>>> user = User.objects.get(username='admin')
>>> user.set_password('new_password')
>>> user.save()
```

### 2. 数据库连接错误
- 检查MySQL服务是否启动
- 检查数据库配置是否正确
- 检查数据库用户权限

### 3. 静态文件无法加载
```bash
python manage.py collectstatic
```

### 4. GDAL安装问题
GDAL是地理空间数据处理库，需要先安装系统库再安装Python绑定。

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install gdal-bin libgdal-dev
export CPLUS_INCLUDE_PATH=/usr/include/gdal
export C_INCLUDE_PATH=/usr/include/gdal
pip install GDAL==3.6.2
```

**CentOS/RHEL:**
```bash
sudo yum install gdal gdal-devel
pip install GDAL==3.6.2
```

**macOS:**
```bash
brew install gdal
pip install GDAL==3.6.2
```

**验证安装:**
```bash
python -c "from osgeo import gdal; print(gdal.__version__)"
```

**注意:** 如果GDAL未安装，AC文件上传功能仍可使用，但无法自动提取地理坐标，经纬度字段将为空。

## AC栅格数据管理模块 API 文档

### 1. 页面访问
- **URL**: `/ac/data/list/`
- **方法**: GET
- **说明**: 返回AC数据管理页面

### 2. 获取AC数据列表
- **URL**: `/ac/data/list/data/`
- **方法**: GET
- **参数**:
  - `page`: 页码（默认1）
  - `limit`: 每页数量（默认10）
  - `ac_name`: 文件名称（模糊查询）
  - `ac_local_name`: 文件本地名称（模糊查询）
  - `start_longitude`: 起始经度（精确查询）
  - `end_longitude`: 结束经度（精确查询）
  - `start_latitude`: 起始纬度（精确查询）
  - `end_latitude`: 结束纬度（精确查询）
- **返回格式**:
```json
{
  "code": 0,
  "msg": "成功",
  "count": 100,
  "data": [
    {
      "id": 1,
      "local_path": "data/ac/1_100.0000000_110.0000000_30.0000000_40.0000000.tif",
      "ac_name": "test_china_region.tif",
      "ac_local_name": "1_100.0000000_110.0000000_30.0000000_40.0000000.tif",
      "start_longitude": "100.0000000",
      "end_longitude": "110.0000000",
      "start_latitude": "30.0000000",
      "end_latitude": "40.0000000",
      "created_at": "2024-01-01 10:00:00",
      "updated_at": "2024-01-01 10:00:00"
    }
  ]
}
```

### 3. 上传AC文件
- **URL**: `/ac/data/upload/`
- **方法**: POST
- **Content-Type**: multipart/form-data
- **参数**:
  - `file`: TIF文件（最大200MB）
- **返回格式**:
```json
{
  "code": 0,
  "msg": "上传成功"
}
```
- **说明**:
  - 只允许上传.tif文件
  - 文件大小限制200MB
  - 自动使用GDAL提取地理坐标
  - 文件命名格式: `{id}_{start_longitude}_{end_longitude}_{start_latitude}_{end_latitude}.tif`
  - 文件保存在 `data/ac/` 目录

### 4. 编辑AC数据
- **URL**: `/ac/data/edit/`
- **方法**: POST
- **参数**:
  - `id`: AC数据ID
  - `ac_name`: 新的文件名称
- **返回格式**:
```json
{
  "code": 0,
  "msg": "修改成功"
}
```
- **说明**: 仅允许修改文件名称字段，不影响服务器上的实际文件

### 5. 删除AC数据
- **URL**: `/ac/data/delete/`
- **方法**: POST
- **参数**:
  - `id`: AC数据ID
- **返回格式**:
```json
{
  "code": 0,
  "msg": "删除成功"
}
```
- **说明**: 
  - 删除数据库记录
  - 同时删除服务器上的实际文件
  - 操作不可恢复

## 项目状态

✅ 项目已完成，所有功能可用

## 技术支持

如有问题，请提交Issue或联系开发团队。

## 许可证

本项目仅供学习和参考使用。