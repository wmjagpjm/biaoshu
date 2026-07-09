"""
模块：API 路由包
用途：薄路由层——参数校验、依赖注入、HTTP 状态码；业务在 services。
对接：main.create_app 中 include_router(..., prefix="/api")
"""
