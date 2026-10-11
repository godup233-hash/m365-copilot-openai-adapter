"""CLI：m365-adapter login / status / logout / serve。"""
from __future__ import annotations

import sys

import click

from app.auth import msal_client
from app.core.config import get_settings
from app.core.errors import AuthRequiredError


@click.group()
def cli() -> None:
    """M365 Copilot OpenAI Adapter 管理命令。"""


@cli.command()
def login() -> None:
    """设备代码登录（工作或学校账号，需 M365 Copilot 许可证）。"""
    settings = get_settings()
    if not settings.azure_client_id:
        click.echo("错误：未配置 AZURE_CLIENT_ID（Entra 应用注册的 Application ID）", err=True)
        sys.exit(2)
    try:
        result = msal_client.device_login(settings)
    except AuthRequiredError as exc:
        click.echo(f"登录失败：{exc}", err=True)
        sys.exit(1)
    click.echo(f"登录成功：{result['account']}")
    click.echo("注意：该账号的企业数据权限将决定 Copilot 的回答范围；"
               "所有经 Adapter 的请求都会以此身份执行。")


@cli.command()
def status() -> None:
    """查看登录状态（不显示令牌）。"""
    settings = get_settings()
    info = msal_client.status(settings)
    if not info["logged_in"]:
        click.echo("未登录。执行 m365-adapter login 完成设备代码登录。")
        return
    click.echo(f"已登录账号：{info['account']}")
    click.echo(f"租户：{info['tenant']}")
    click.echo(f"Token Cache：{info['cache_path']}")
    if info.get("token"):
        click.echo(f"令牌状态：可静默获取（source={info['token'].get('token_source')}）")
    else:
        click.echo("令牌状态：静默获取失败，请重新 login")


@cli.command()
def logout() -> None:
    """注销并删除本地 Token Cache。"""
    settings = get_settings()
    if msal_client.logout(settings):
        click.echo("已注销，Token Cache 已删除。")
    else:
        click.echo("本地无登录状态。")


@cli.command()
def serve() -> None:
    """启动 API 服务（等价于 uvicorn app.main:app）。"""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    cli()
