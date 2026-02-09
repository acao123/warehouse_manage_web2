-- MySQL初始化SQL文件
-- 仓库管理系统数据库

-- 创建数据库
CREATE DATABASE IF NOT EXISTS `warehouse_manage` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `warehouse_manage`;

-- 菜单表
CREATE TABLE IF NOT EXISTS `sys_menu` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `menu_name` varchar(50) NOT NULL COMMENT '菜单名称',
  `parent_id` bigint DEFAULT NULL COMMENT '父级菜单ID',
  `menu_order` int NOT NULL DEFAULT '0' COMMENT '显示顺序',
  `route_path` varchar(200) NOT NULL DEFAULT '' COMMENT '路由地址',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_parent_id` (`parent_id`),
  KEY `idx_menu_order` (`menu_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='菜单表';

-- 角色表
CREATE TABLE IF NOT EXISTS `sys_role` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `role_name` varchar(50) NOT NULL COMMENT '角色名称',
  `role_key` varchar(50) NOT NULL COMMENT '角色英文名称',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_name` (`role_name`),
  UNIQUE KEY `uk_role_key` (`role_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色表';

-- 角色菜单关联表
CREATE TABLE IF NOT EXISTS `sys_role_menus` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `role_id` bigint NOT NULL COMMENT '角色ID',
  `menu_id` bigint NOT NULL COMMENT '菜单ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_menu` (`role_id`, `menu_id`),
  KEY `idx_role_id` (`role_id`),
  KEY `idx_menu_id` (`menu_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色菜单关联表';

-- 用户表
CREATE TABLE IF NOT EXISTS `sys_user` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `nickname` varchar(50) NOT NULL COMMENT '用户昵称',
  `department` varchar(100) NOT NULL COMMENT '归属部门',
  `phone` varchar(11) NOT NULL DEFAULT '' COMMENT '手机号码',
  `username` varchar(50) NOT NULL COMMENT '用户名称',
  `password` varchar(255) NOT NULL COMMENT '密码',
  `gender` varchar(10) NOT NULL DEFAULT 'male' COMMENT '用户性别',
  `status` varchar(10) NOT NULL DEFAULT 'active' COMMENT '用户状态',
  `position` varchar(50) NOT NULL DEFAULT '' COMMENT '岗位',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- 用户角色关联表
CREATE TABLE IF NOT EXISTS `sys_user_roles` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `role_id` bigint NOT NULL COMMENT '角色ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_role` (`user_id`, `role_id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_role_id` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户角色关联表';

-- 插入测试数据

-- 插入菜单数据
INSERT INTO `sys_menu` (`id`, `menu_name`, `parent_id`, `menu_order`, `route_path`) VALUES
(1, '系统管理', NULL, 1, ''),
(2, '用户管理', 1, 1, '/system/user/list/'),
(3, '角色管理', 1, 2, '/system/role/list/'),
(4, '菜单管理', 1, 3, '/system/menu/list/');

-- 插入角色数据
INSERT INTO `sys_role` (`id`, `role_name`, `role_key`) VALUES
(1, '系统管理员', 'admin'),
(2, '普通用户', 'user');

-- 插入角色菜单关联数据
INSERT INTO `sys_role_menus` (`role_id`, `menu_id`) VALUES
(1, 1), (1, 2), (1, 3), (1, 4),  -- 管理员拥有所有菜单权限
(2, 1), (2, 2);  -- 普通用户只有用户管理权限

-- 插入用户数据
-- 注意：密码已使用Django的PBKDF2算法加密
-- admin密码：admin888
-- test密码：test123
INSERT INTO `sys_user` (`id`, `nickname`, `department`, `phone`, `username`, `password`, `gender`, `status`, `position`) VALUES
(1, '系统管理员', '总部', '13800138000', 'admin', 'pbkdf2_sha256$600000$YourSaltHere$YourHashHere', 'male', 'active', '系统管理员'),
(2, '测试用户', '总部-研发', '13900139000', 'test', 'pbkdf2_sha256$600000$YourSaltHere$YourHashHere', 'female', 'active', '测试工程师');

-- 插入用户角色关联数据
INSERT INTO `sys_user_roles` (`user_id`, `role_id`) VALUES
(1, 1),  -- admin是系统管理员
(2, 2);  -- test是普通用户

-- 说明：
-- 1. 由于密码使用Django的PBKDF2算法加密，包含随机盐值，因此上面的密码hash只是示例
-- 2. 实际部署时，建议使用Django的init_data管理命令来初始化数据
-- 3. 或者在插入用户后，使用Django shell手动设置密码：
--    from system.models import User
--    user = User.objects.get(username='admin')
--    user.set_password('admin888')
--    user.save()

-- AC栅格数据表
CREATE TABLE IF NOT EXISTS `ac_tif` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `local_path` varchar(200) NOT NULL DEFAULT '' COMMENT 'AC数据服务器地址',
  `ac_name` varchar(200) NOT NULL COMMENT 'AC文件名称：上传时文件名称',
  `ac_local_name` varchar(200) NOT NULL COMMENT 'AC文件本地名称：id_起始经度_结束经度_起始纬度_结束纬度',
  `start_longitude` DECIMAL(11, 7) DEFAULT NULL COMMENT '起始经度',
  `end_longitude` DECIMAL(11, 7) DEFAULT NULL COMMENT '结束经度',
  `start_latitude` DECIMAL(11, 7) DEFAULT NULL COMMENT '起始纬度',
  `end_latitude` DECIMAL(11, 7) DEFAULT NULL COMMENT '结束纬度',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_ac_name` (`ac_name`),
  KEY `idx_longitude` (`start_longitude`,`end_longitude`,`start_latitude`,`end_latitude`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AC栅格数据表';

-- 插入AC栅格测试数据
INSERT INTO `ac_tif` (`id`, `local_path`, `ac_name`, `ac_local_name`, `start_longitude`, `end_longitude`, `start_latitude`, `end_latitude`) VALUES
(1, 'data/ac/1_100.0000000_110.0000000_30.0000000_40.0000000.tif', 'test_china_region.tif', '1_100.0000000_110.0000000_30.0000000_40.0000000.tif', 100.0000000, 110.0000000, 30.0000000, 40.0000000),
(2, 'data/ac/2_120.0000000_130.0000000_25.0000000_35.0000000.tif', 'test_east_china.tif', '2_120.0000000_130.0000000_25.0000000_35.0000000.tif', 120.0000000, 130.0000000, 25.0000000, 35.0000000);
