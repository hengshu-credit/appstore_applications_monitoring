"""Interactive server-side administrator recovery; no web bypass or fixed key."""
import getpass
import sys

from .auth import recover_administrator


def run(db):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('应急重置必须在交互式终端执行，密码不能通过命令参数或管道传入')
    row = db.execute("SELECT username FROM auth_users WHERE role='admin'").fetchone()
    if not row:
        raise ValueError('尚未创建超级管理员，请在首次启动页面注册')
    print('当前超级管理员：' + row['username'])
    print('此操作会撤销超管所有登录会话和旧恢复码。')
    if input('输入 RESET 确认重置：').strip() != 'RESET':
        print('已取消')
        return
    name = input('新账号（留空保留原账号）：').strip()
    password = getpass.getpass('新密码（10–128 位）：')
    if password != getpass.getpass('再次输入新密码：'):
        raise ValueError('两次输入的密码不一致，未作修改')
    result = recover_administrator(db, name, password)
    print('已重置，账号：' + result['username'])
    print('新的恢复码（请立即安全保存）：' + result['recovery_code'])
