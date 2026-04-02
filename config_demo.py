# 把此文件复制并重命名为config.py并修改其中的参数为实际值
# ========== 用户配置区域 ==========
target_qq = "123456"                     # 你的QQ号（相册主人）

# 方式1：直接填写字典（推荐）
# cookies = {
#     'p_skey': 'xxx',
#     'uin': 'o0000000',
#     'skey': 'xxx',
# }

# 方式2：粘贴浏览器复制的单行cookie字符串（自动解析）
cookies_str = (
    "p_skey=xxx; uin=o0000000; skey=xxx; pgv_pvi=xxx; pgv_si=sxxx; "
)

# 如果你使用方式1，请将上面 cookies_str 注释或删除，并将下面的 cookies 变量赋值为你的字典
cookies = None          # 如果使用字典，这里改为你的字典，并注释掉 cookies_str 的赋值
# =================================
