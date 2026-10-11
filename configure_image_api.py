"""Store the drawing API key in the current Windows user's environment."""
import getpass
import winreg


def main() -> None:
    value = getpass.getpass("OpenAI 绘图 API key（隐藏输入，留空退出）：").strip()
    if not value:
        print("未修改设置。")
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, "OPENAI_API_KEY", 0, winreg.REG_SZ, value)
    print("已写入当前用户的 OPENAI_API_KEY。请回到对话说：已配置好。")
    print("这个脚本不会调用 API，也不会产生绘图费用。")


if __name__ == "__main__":
    main()
