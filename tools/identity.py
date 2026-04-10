import random                                                                          # 随机库用于生成姓名、号码、地址等模拟数据
import string                                                                          # 字符库用于生成密码、用户名等字符集合
import time                                                                            # 时间库用于时间戳和年龄计算
import hashlib                                                                         # 哈希库用于生成稳定指纹和设备 ID
from datetime import datetime, timedelta                                               # 日期时间库用于生成生日和计算年龄
import re                                                                            # 正则表达式库用于验证和格式化手机号

class Identity:
    """随机身份生成模块，可用于注册测试、爬虫模拟、自动化测试、风控演练、数据填充等通用场景"""

    def __init__(self, seed=None, locale="mixed"):
        self.seed = seed                                                               # 保存随机种子，便于外部记录和复现结果
        self.locale = locale.lower()                                                   # 语言风格，可选 zh / en / mixed
        self.random = random.Random(seed)                                              # 创建独立随机实例，避免污染全局随机状态

        self.zh_family_names = [                                                       # 常见中文姓氏列表
            "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "吴", "周",
            "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗",
            "郑", "梁", "谢", "宋", "唐", "许", "韩", "冯", "邓", "曹",
            "彭", "曾", "萧", "田", "董", "袁", "潘", "于", "蒋", "蔡",
        ]
        self.zh_given_chars = [                                                        # 常见中文名字字库
            "伟", "强", "磊", "洋", "勇", "军", "杰", "涛", "明", "超",
            "峰", "刚", "平", "辉", "鹏", "华", "飞", "鑫", "波", "斌",
            "凯", "浩", "俊", "健", "宇", "晨", "凡", "诚", "睿", "恒",
            "欣", "悦", "怡", "倩", "婷", "敏", "静", "颖", "洁", "娜",
            "琳", "雪", "佳", "雨", "可", "宁", "梦", "婧", "彤", "瑶",
        ]

        self.en_first_names_male = [                                                   # 常见英文男性名
            "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles",
            "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kevin",
        ]
        self.en_first_names_female = [                                                 # 常见英文女性名
            "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen",
            "Nancy", "Lisa", "Margaret", "Betty", "Sandra", "Ashley", "Kimberly", "Emily", "Donna", "Michelle",
        ]
        self.en_last_names = [                                                         # 常见英文姓氏
            "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Taylor",
            "Anderson", "Thomas", "Moore", "Martin", "Jackson", "Thompson", "White", "Harris", "Clark", "Lewis",
        ]

        self.email_domains = [                                                         # 常见邮箱域名列表
            "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
            "proton.me", "aol.com", "live.com", "example.com", "mail.com",
        ]

        self.mobile_prefixes_cn = [                                                    # 常见中国手机号号段前缀
            "130", "131", "132", "133", "135", "136", "137", "138", "139",
            "150", "151", "152", "155", "156", "157", "158", "159",
            "166", "171", "172", "173", "175", "176", "177", "178",
            "180", "181", "182", "183", "185", "186", "187", "188", "189",
            "191", "193", "195", "196", "198", "199",
        ]

        self.us_area_codes = [                                                         # 常见美国区号样式列表
            "212", "213", "305", "310", "312", "347", "408", "415", "510", "617",
            "646", "650", "702", "713", "718", "786", "818", "917", "929", "972",
        ]

        self.cn_provinces = [                                                          # 中国省级行政区样例
            "北京市", "上海市", "广东省", "浙江省", "江苏省", "山东省", "四川省", "湖北省", "福建省", "湖南省",
            "河北省", "河南省", "陕西省", "安徽省", "江西省", "重庆市", "天津市", "广西壮族自治区", "云南省", "辽宁省",
        ]
        self.cn_cities = [                                                             # 中国城市样例
            "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "武汉", "厦门",
            "长沙", "郑州", "西安", "重庆", "天津", "青岛", "宁波", "东莞", "佛山", "合肥",
        ]
        self.cn_districts = [                                                          # 中国城区样例
            "朝阳区", "海淀区", "浦东新区", "天河区", "南山区", "西湖区", "鼓楼区", "高新区", "武昌区", "思明区",
            "岳麓区", "金水区", "雁塔区", "渝北区", "滨海新区", "市南区", "鄞州区", "南城街道", "顺德区", "包河区",
        ]
        self.street_suffix_cn = [                                                      # 中文街道路名后缀
            "路", "街", "大道", "巷", "弄", "街道", "中路", "东路", "西路", "南路", "北路",
        ]

        self.en_street_names = [                                                       # 常见英文街道名
            "Main", "Oak", "Pine", "Maple", "Cedar", "Elm", "Washington", "Lake", "Hill", "Sunset",
            "Park", "River", "Walnut", "Cherry", "Center", "North", "South", "East", "West", "Highland",
        ]
        self.en_street_suffix = [                                                      # 常见英文街道后缀
            "St", "Ave", "Blvd", "Rd", "Ln", "Dr", "Ct", "Way", "Pl", "Terrace",
        ]
        self.en_cities = [                                                             # 常见英文城市样例
            "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Seattle", "Boston", "Miami", "Austin", "Denver",
        ]
        self.en_states = [                                                             # 常见英文州名样例
            "California", "Texas", "Florida", "New York", "Illinois", "Washington", "Massachusetts", "Arizona", "Colorado", "Nevada",
        ]
        self.en_countries = [                                                          # 常见国家样例
            "United States", "United Kingdom", "Canada", "Australia", "Germany", "France", "Japan", "Singapore",
        ]

        self.company_prefixes = [                                                      # 公司名前缀词库
            "Nova", "Sky", "Blue", "Prime", "Next", "Quantum", "Bright", "Zen", "Vertex", "Core",
            "Atlas", "Peak", "Fusion", "Cloud", "Silver", "Golden", "Meta", "Alpha", "Vision", "Urban",
        ]
        self.company_suffixes = [                                                      # 公司名后缀词库
            "Tech", "Labs", "Media", "Systems", "Digital", "Works", "Studio", "Network", "Health", "Logistics",
            "Capital", "Retail", "AI", "Consulting", "Solutions", "Dynamics", "Data", "Global", "Foods", "Energy",
        ]

        self.browser_pool = [                                                          # 常见浏览器资料池
            {"browser": "Chrome", "version": "124.0.0.0", "engine": "Blink", "platform": "Windows"},
            {"browser": "Chrome", "version": "125.0.0.0", "engine": "Blink", "platform": "macOS"},
            {"browser": "Edge", "version": "124.0.0.0", "engine": "Blink", "platform": "Windows"},
            {"browser": "Firefox", "version": "126.0", "engine": "Gecko", "platform": "Windows"},
            {"browser": "Safari", "version": "17.4", "engine": "WebKit", "platform": "macOS"},
        ]

    # ==================== 基础工具层 ====================

    def choice(self, items):                                                           # 从列表中随机选一个元素
        return self.random.choice(items)                                               # 使用独立随机实例进行抽样

    def randint(self, start, end):                                                     # 生成区间随机整数
        return self.random.randint(start, end)                                         # 返回包含两端的随机整数

    def digits(self, length):                                                          # 生成固定长度纯数字字符串
        return "".join(self.random.choice(string.digits) for _ in range(length))       # 每一位都从数字字符集中随机选择

    def letters(self, length, lowercase=True):                                         # 生成固定长度字母字符串
        pool = string.ascii_lowercase if lowercase else string.ascii_letters           # 根据需求选择字母池
        return "".join(self.random.choice(pool) for _ in range(length))                # 拼接成长度固定的字母串

    def alnum(self, length, lowercase=True):                                           # 生成固定长度字母数字混合字符串
        pool = string.ascii_lowercase + string.digits if lowercase else string.ascii_letters + string.digits  # 选择混合字符池
        return "".join(self.random.choice(pool) for _ in range(length))                # 返回字母数字混合串

    def hex(self, length=16):                                                          # 生成固定长度十六进制字符串
        return "".join(self.random.choice("0123456789abcdef") for _ in range(length))  # 返回十六进制风格标识

    def slug(self, words=2, sep="_"):                                                  # 生成短横线或下划线风格的可读标识
        word_pool = [                                                                  # 可读英文词池
            "red", "blue", "green", "fast", "silent", "smart", "north", "south", "urban", "clear",
            "rapid", "pixel", "cloud", "nova", "bright", "lucky", "stone", "river", "gold", "delta",
        ]
        return sep.join(self.choice(word_pool) for _ in range(words))                  # 随机取若干词并按分隔符拼接

    def _pick_gender(self, gender=None):                                               # 统一处理性别输入
        if gender in ("male", "female"): return gender                                 # 如果外部指定了合法性别就直接使用
        return self.choice(["male", "female"])                                         # 否则随机返回男或女

    def _pick_locale(self, locale=None):                                               # 统一处理语言风格输入
        current = (locale or self.locale or "mixed").lower()                           # 优先使用传入 locale，否则使用对象默认 locale
        if current in ("zh", "en", "mixed"): return current                            # 合法值直接返回
        return "mixed"                                                                 # 其他非法值统一回退 mixed

    def _html_to_text(self, text):                                                     # 简单去掉 HTML 标签，便于后续场景复用
        if not isinstance(text, str): return ""                                        # 非字符串直接返回空
        return re.sub(r"<[^>]+>", " ", text).strip()                                   # 用简单正则去掉标签并清理空白

    # ==================== 姓名与基础身份层 ====================

    def firstName(self, gender=None, locale=None):                                     # 生成名字或英文名
        locale = self._pick_locale(locale)                                             # 解析语言风格
        gender = self._pick_gender(gender)                                             # 解析性别

        if locale == "zh":                                                             # 中文风格下返回单个中文名字部分
            return self.choice(self.zh_given_chars) + (self.choice(self.zh_given_chars) if self.random.random() < 0.65 else "")  # 一字名或二字名
        if locale == "en":                                                             # 英文风格下返回英文 first name
            return self.choice(self.en_first_names_male if gender == "male" else self.en_first_names_female)  # 按性别选择英文名

        if self.random.random() < 0.5:                                                 # mixed 模式下随机走中文或英文路线
            return self.firstName(gender=gender, locale="zh")
        return self.firstName(gender=gender, locale="en")

    def lastName(self, locale=None):                                                   # 生成姓氏
        locale = self._pick_locale(locale)                                             # 解析语言风格
        if locale == "zh": return self.choice(self.zh_family_names)                    # 中文风格返回中文姓
        if locale == "en": return self.choice(self.en_last_names)                      # 英文风格返回英文姓
        return self.lastName(locale=self.choice(["zh", "en"]))                         # mixed 模式下随机选中文姓或英文姓

    def fullName(self, gender=None, locale=None):                                      # 生成完整姓名
        locale = self._pick_locale(locale)                                             # 解析语言风格
        gender = self._pick_gender(gender)                                             # 解析性别

        if locale == "zh":                                                             # 中文风格姓名格式通常是姓在前
            return self.lastName("zh") + self.firstName(gender=gender, locale="zh")   # 拼出中文全名

        if locale == "en":                                                             # 英文风格姓名格式通常是名在前姓在后
            return f"{self.firstName(gender=gender, locale='en')} {self.lastName('en')}"  # 拼出英文全名

        return self.fullName(gender=gender, locale=self.choice(["zh", "en"]))         # mixed 模式随机采用中文或英文格式

    def gender(self):                                                                  # 生成性别字段
        return self.choice(["male", "female"])                                         # 返回通用英文性别值便于跨系统使用

    def birthDate(self, minAge=18, maxAge=60, fmt="%Y-%m-%d"):                         # 生成出生日期
        today = datetime.utcnow().date()                                               # 使用 UTC 当前日期作为基准
        min_days = minAge * 365                                                        # 最小年龄换算为大致天数
        max_days = maxAge * 365                                                        # 最大年龄换算为大致天数
        age_days = self.randint(min_days, max_days)                                    # 在年龄范围内随机一天
        dob = today - timedelta(days=age_days)                                         # 从今天往前推得到出生日期
        return dob.strftime(fmt)                                                       # 按指定格式输出字符串

    def age(self, minAge=18, maxAge=60):                                               # 生成年龄
        return self.randint(minAge, maxAge)                                            # 直接在区间内随机一个年龄值

    # ==================== 用户账号层 ====================

    def username(self, name=None, style="auto", withDigits=True, minLen=8, maxLen=14):# 生成用户名
        base = ""                                                                      # 初始化用户名基底

        if name:                                                                       # 如果调用者传了姓名，则优先基于姓名构造
            cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "", str(name))             # 只保留字母数字和中文
            if re.search(r"[\u4e00-\u9fff]", cleaned):                                 # 如果包含中文，则转为拼音风格占位不现实，这里改走 slug 路线
                base = self.slug(words=2, sep="")                                      # 中文姓名不做伪拼音，避免错误感太强
            else:
                base = cleaned.lower()                                                 # 英文姓名直接转小写作为基底
        else:
            if style == "auto":                                                        # auto 模式随机选择一种用户名风格
                style = self.choice(["word", "name", "tech", "clean"])                 # 可读风格集合
            if style == "word":
                base = self.slug(words=2, sep="")                                      # 词语拼接风格
            elif style == "name":
                base = self.choice(self.en_first_names_male + self.en_first_names_female).lower() + self.choice(self.en_last_names).lower()  # 英文名风格
            elif style == "tech":
                base = self.choice(["dev", "bot", "cloud", "sys", "neo", "pixel", "data"]) + self.alnum(self.randint(3, 6))  # 技术感风格
            else:
                base = self.letters(self.randint(5, 8))                                # 简洁字母风格

        if withDigits:                                                                 # 需要数字尾巴时附加 2~4 位数字
            base += self.digits(self.randint(2, 4))                                    # 为用户名增加唯一性

        if len(base) < minLen:                                                         # 太短时补足长度
            base += self.alnum(minLen - len(base))                                     # 通过字母数字补足
        if len(base) > maxLen:                                                         # 太长时截断
            base = base[:maxLen]                                                       # 截到最大长度

        return base.lower()                                                            # 用户名统一输出小写

    def password(self, length=12, strong=True, symbols="!@#$%^&*"):                    # 生成密码
        if length < 6: length = 6                                                      # 密码长度过短时强制至少 6 位

        if strong:                                                                     # 强密码模式要求至少包含大写、小写、数字、符号
            parts = [
                self.choice(string.ascii_lowercase),                                   # 至少一个小写字母
                self.choice(string.ascii_uppercase),                                   # 至少一个大写字母
                self.choice(string.digits),                                            # 至少一个数字
                self.choice(symbols),                                                  # 至少一个符号
            ]
            pool = string.ascii_letters + string.digits + symbols                      # 剩余字符从完整字符池中生成
            while len(parts) < length:                                                 # 填充直到达到目标长度
                parts.append(self.choice(pool))                                        # 追加随机字符
            self.random.shuffle(parts)                                                 # 打乱顺序避免规则太明显
            return "".join(parts)                                                      # 拼接成最终密码

        pool = string.ascii_letters + string.digits                                    # 弱密码模式只用字母数字
        return "".join(self.choice(pool) for _ in range(length))                       # 返回普通密码

    def email(self, username=None, domain=None):                                       # 生成邮箱地址
        user = username or self.username(withDigits=True, minLen=8, maxLen=14)         # 没传用户名时自动生成一个
        domain = domain or self.choice(self.email_domains)                             # 没传域名时随机选常见邮箱域
        return f"{user}@{domain}".lower()                                              # 拼成邮箱地址并统一小写

    def phone(self, country="CN"):                                                     # 生成手机号或电话号码
        country = str(country).upper()                                                 # 国家代码统一转大写
        if country == "CN":                                                            # 中国手机号格式
            return self.choice(self.mobile_prefixes_cn) + self.digits(8)               # 3 位前缀 + 8 位数字 = 11 位手机号
        if country == "US":                                                            # 美国电话号码格式
            area = self.choice(self.us_area_codes)                                     # 随机区号
            prefix = self.randint(200, 999)                                            # 中间三位
            suffix = self.randint(1000, 9999)                                          # 后四位
            return f"+1-{area}-{prefix}-{suffix}"                                      # 拼成常见美国号码格式
        return "+" + self.digits(self.randint(10, 14))                                 # 其他国家返回通用国际电话占位

    # ==================== 地址层 ====================

    def postalCode(self, locale=None):                                                 # 生成邮编
        locale = self._pick_locale(locale)                                             # 解析语言风格
        if locale == "zh": return self.digits(6)                                       # 中文地址常见 6 位邮编
        if locale == "en": return self.digits(5)                                       # 英文环境常见 5 位邮编
        return self.postalCode(locale=self.choice(["zh", "en"]))                       # mixed 模式随机选择一种

    def city(self, locale=None):                                                       # 生成城市
        locale = self._pick_locale(locale)                                             # 解析语言风格
        if locale == "zh": return self.choice(self.cn_cities)                          # 中文城市
        if locale == "en": return self.choice(self.en_cities)                          # 英文城市
        return self.city(locale=self.choice(["zh", "en"]))                             # mixed 模式随机一种

    def province(self, locale=None):                                                   # 生成省份 / 州
        locale = self._pick_locale(locale)                                             # 解析语言风格
        if locale == "zh": return self.choice(self.cn_provinces)                       # 中文省份
        if locale == "en": return self.choice(self.en_states)                          # 英文州名
        return self.province(locale=self.choice(["zh", "en"]))                         # mixed 模式随机一种

    def country(self, locale=None):                                                    # 生成国家
        locale = self._pick_locale(locale)                                             # 解析语言风格
        if locale == "zh": return "中国"                                                # 中文风格默认给中国
        if locale == "en": return self.choice(self.en_countries)                       # 英文风格随机国家
        return self.choice(["中国"] + self.en_countries)                               # mixed 模式混合选择

    def street(self, locale=None):                                                     # 生成街道地址
        locale = self._pick_locale(locale)                                             # 解析语言风格

        if locale == "zh":                                                             # 中文地址风格
            road_name = self.choice(self.cn_cities) + self.choice(self.street_suffix_cn)  # 用城市名 + 路名后缀拼出路名
            number = self.randint(1, 999)                                              # 生成门牌号
            room = self.randint(101, 3204)                                             # 生成房间号
            return f"{road_name}{number}号{room}室"                                    # 返回中文街道地址

        if locale == "en":                                                             # 英文地址风格
            number = self.randint(10, 9999)                                            # 门牌号
            street_name = self.choice(self.en_street_names)                            # 街道名
            suffix = self.choice(self.en_street_suffix)                                # 街道后缀
            return f"{number} {street_name} {suffix}"                                  # 返回英文地址

        return self.street(locale=self.choice(["zh", "en"]))                           # mixed 模式随机一种风格

    def address(self, locale=None):                                                    # 生成完整地址
        locale = self._pick_locale(locale)                                             # 解析语言风格

        if locale == "zh":                                                             # 中文完整地址
            province = self.province("zh")                                             # 省份
            city = self.city("zh")                                                     # 城市
            district = self.choice(self.cn_districts)                                  # 区县
            street = self.street("zh")                                                 # 街道
            postal = self.postalCode("zh")                                             # 邮编
            return {
                "country": "中国",                                                      # 国家
                "province": province,                                                  # 省份
                "city": city,                                                          # 城市
                "district": district,                                                  # 区县
                "street": street,                                                      # 街道
                "postal_code": postal,                                                 # 邮编
                "full": f"{province}{city}{district}{street} {postal}",                # 完整地址字符串
            }

        if locale == "en":                                                             # 英文完整地址
            state = self.province("en")                                                # 州
            city = self.city("en")                                                     # 城市
            street = self.street("en")                                                 # 街道
            postal = self.postalCode("en")                                             # 邮编
            country = self.country("en")                                               # 国家
            return {
                "country": country,                                                    # 国家
                "province": state,                                                     # 州
                "city": city,                                                          # 城市
                "district": "",                                                        # 英文场景默认不强行模拟 district
                "street": street,                                                      # 街道
                "postal_code": postal,                                                 # 邮编
                "full": f"{street}, {city}, {state} {postal}, {country}",              # 完整地址字符串
            }

        return self.address(locale=self.choice(["zh", "en"]))                          # mixed 模式随机生成中英文地址

    # ==================== 证件与标识层 ====================

    def nationalId(self, country="CN"):                                                # 生成身份证风格编号
        country = str(country).upper()                                                 # 国家代码统一转大写

        if country == "CN":                                                            # 中国身份证风格 18 位
            area = self.choice([                                                       # 取一些常见行政区划码样例
                "110101", "110105", "310101", "310115", "440103", "440106", "440300", "330106", "320102", "510107",
            ])
            birth = self.birthDate(minAge=18, maxAge=60, fmt="%Y%m%d")                 # 使用生日作为身份证中的出生日期部分
            seq = self.digits(3)                                                       # 顺序码 3 位
            base = area + birth + seq                                                  # 前 17 位基础号码
            weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]            # 中国身份证校验权重
            checks = "10X98765432"                                                     # 校验码映射表
            total = sum(int(base[i]) * weights[i] for i in range(17))                  # 计算加权和
            check = checks[total % 11]                                                 # 算出最后一位校验码
            return base + check                                                        # 返回完整 18 位风格编号

        if country == "US":                                                            # 美国场景返回 SSN 风格占位
            return f"{self.digits(3)}-{self.digits(2)}-{self.digits(4)}"               # 返回 3-2-4 格式编号

        return self.alnum(12, lowercase=False)                                         # 其他国家返回通用字母数字证件号

    def passport(self, country="CN"):                                                  # 生成护照风格编号
        country = str(country).upper()                                                 # 国家代码统一转大写
        if country == "CN": return "E" + self.digits(8)                                # 中国护照常见 E+8 位风格
        if country == "US": return self.digits(9)                                      # 美国护照常见 9 位数字风格
        return self.choice(string.ascii_uppercase) + self.alnum(8, lowercase=False)    # 其他国家返回通用占位格式

    def uuidLike(self):                                                                # 生成 UUID 风格字符串
        parts = [self.hex(8), self.hex(4), self.hex(4), self.hex(4), self.hex(12)]    # 按 UUID 8-4-4-4-12 分段生成
        return "-".join(parts)                                                         # 返回标准形态 UUID 风格值

    def deviceId(self, seedText=None):                                                 # 生成设备 ID 或设备指纹
        source = seedText or (self.alnum(12) + str(time.time()) + self.hex(8))         # 组装随机源文本
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]                 # 取 SHA256 前 32 位作为设备 ID

    def fingerprint(self, profile=None):                                               # 生成通用身份指纹
        profile = profile or self.profile()                                             # 没传档案时自动先生成一份完整身份
        raw = json_safe_string(profile)                                                # 转成稳定字符串用于计算指纹
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()                         # 返回 SHA256 指纹

    # ==================== 行业通用资料层 ====================

    def company(self):                                                                 # 生成公司信息
        name = f"{self.choice(self.company_prefixes)} {self.choice(self.company_suffixes)}"  # 随机组合出公司名
        domain = re.sub(r"[^a-z0-9]+", "", name.lower()) + ".com"                      # 把公司名清洗成域名风格
        return {
            "name": name,                                                              # 公司名称
            "domain": domain,                                                          # 公司域名
            "email": f"contact@{domain}",                                              # 公司联系邮箱
            "phone": self.phone(country="US"),                                         # 公司联系电话占位
        }

    def socialProfile(self, name=None):                                                # 生成社交资料
        display = name or self.fullName()                                              # 显示昵称优先使用传入姓名
        handle = self.username(name=display, style="auto", withDigits=True)            # 社交账号名
        return {
            "display_name": display,                                                   # 显示名
            "username": handle,                                                        # 用户名
            "bio": self.choice([                                                       # 简短签名
                "Building quietly.", "Exploring new ideas.", "Coffee, code, repeat.",
                "Curious mind.", "Learning every day.", "Minimalist workflow.",
                "喜欢技术与效率。", "持续学习，保持输出。", "专注自动化与工具。", "热爱创造与分享。",
            ]),
            "website": f"https://{handle}.example.com",                                # 个人主页占位
        }

    def browserProfile(self):                                                          # 生成浏览器资料
        item = dict(self.choice(self.browser_pool))                                     # 从浏览器池中复制一份配置
        if item["browser"] == "Chrome" and item["platform"] == "Windows":              # 根据浏览器和平台组合生成更自然的 UA
            item["user_agent"] = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{item['version']} Safari/537.36"
        elif item["browser"] == "Chrome" and item["platform"] == "macOS":
            item["user_agent"] = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{item['version']} Safari/537.36"
        elif item["browser"] == "Edge":
            item["user_agent"] = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/{item['version']}"
        elif item["browser"] == "Firefox":
            item["user_agent"] = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{item['version']}) Gecko/20100101 Firefox/{item['version']}"
        else:
            item["user_agent"] = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{item['version']} Safari/605.1.15"
        return item                                                                     # 返回完整浏览器资料

    # ==================== 完整身份编排层 ====================

    def profile(self, locale=None, gender=None, country=None):                         # 生成完整身份档案
        locale = self._pick_locale(locale)                                             # 解析语言风格
        gender = self._pick_gender(gender)                                             # 解析性别
        country = (country or ("CN" if locale == "zh" else "US")).upper()              # 根据语言风格推断国家，除非外部显式指定

        full_name = self.fullName(gender=gender, locale=locale)                        # 生成完整姓名
        birth_date = self.birthDate(minAge=18, maxAge=60, fmt="%Y-%m-%d")              # 生成生日
        age_years = self._calc_age_from_date(birth_date)                               # 由生日反算年龄，保证两者一致
        addr = self.address(locale=locale)                                             # 生成地址对象
        user = self.username(name=full_name, style="auto", withDigits=True)            # 基于姓名生成用户名
        email = self.email(username=user)                                              # 基于用户名生成邮箱
        browser = self.browserProfile()                                                # 生成浏览器资料
        company = self.company()                                                       # 生成公司资料
        social = self.socialProfile(name=full_name)                                    # 生成社交资料

        profile = {
            "identity_id": self.uuidLike(),                                            # 身份对象唯一 ID
            "locale": locale,                                                          # 语言风格
            "gender": gender,                                                          # 性别
            "full_name": full_name,                                                    # 姓名
            "first_name": self._split_first_name(full_name, locale),                   # 名字字段
            "last_name": self._split_last_name(full_name, locale),                     # 姓氏字段
            "birth_date": birth_date,                                                  # 出生日期
            "age": age_years,                                                          # 年龄
            "email": email,                                                            # 邮箱
            "username": user,                                                          # 用户名
            "password": self.password(length=12, strong=True),                         # 密码
            "phone": self.phone(country=country),                                      # 电话
            "country_code": country,                                                   # 国家代码
            "national_id": self.nationalId(country=country),                           # 身份证风格编号
            "passport": self.passport(country=country),                                # 护照风格编号
            "address": addr,                                                           # 地址对象
            "company": company,                                                        # 公司对象
            "social": social,                                                          # 社交资料对象
            "browser": browser,                                                        # 浏览器资料
            "device_id": self.deviceId(seedText=email + full_name),                    # 设备 ID
            "created_at": int(time.time()),                                            # 生成时间戳
        }
        profile["fingerprint"] = self.fingerprint(profile)                             # 生成该身份档案的稳定指纹
        return profile                                                                 # 返回完整身份档案

    def simple(self, locale=None):                                                     # 生成简化身份档案，适合表单快速填充
        locale = self._pick_locale(locale)                                             # 解析语言风格
        gender = self.gender()                                                         # 随机性别
        full_name = self.fullName(gender=gender, locale=locale)                        # 姓名
        user = self.username(name=full_name)                                           # 用户名
        return {
            "name": full_name,                                                         # 姓名
            "gender": gender,                                                          # 性别
            "username": user,                                                          # 用户名
            "password": self.password(),                                               # 密码
            "email": self.email(username=user),                                        # 邮箱
            "phone": self.phone("CN" if locale == "zh" else "US"),                     # 电话
        }

    # ==================== 内部辅助层 ====================

    def _calc_age_from_date(self, birth_date_text):                                    # 根据生日字符串计算年龄
        try:
            dob = datetime.strptime(birth_date_text, "%Y-%m-%d").date()                # 解析生日字符串
        except Exception:
            return self.age()                                                          # 解析失败时回退随机年龄
        today = datetime.utcnow().date()                                               # 当前日期
        years = today.year - dob.year                                                  # 先按年份相减
        if (today.month, today.day) < (dob.month, dob.day):                            # 如果今年生日还没到则减一岁
            years -= 1
        return max(0, years)                                                           # 返回非负年龄

    def _split_first_name(self, full_name, locale):                                    # 从全名里拆分名字
        if locale == "zh":                                                             # 中文姓名中名字一般是除姓之外的部分
            return full_name[1:] if len(full_name) > 1 else full_name                  # 取第一个字之后的内容
        parts = str(full_name).split()                                                 # 英文姓名按空格拆分
        return parts[0] if parts else ""                                               # 取第一个词作为 first name

    def _split_last_name(self, full_name, locale):                                     # 从全名里拆分姓氏
        if locale == "zh":                                                             # 中文姓名中姓通常是第一个字
            return full_name[:1]                                                       # 取第一个字作为姓
        parts = str(full_name).split()                                                 # 英文姓名按空格拆分
        return parts[-1] if parts else ""                                              # 取最后一个词作为 last name


def json_safe_string(value):                                                           # 把任意对象稳定转成字符串用于哈希
    if isinstance(value, dict):                                                        # 字典需要按 key 排序后递归序列化
        items = sorted((str(k), json_safe_string(v)) for k, v in value.items())        # 排序保证结果稳定
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"                      # 拼成稳定字典字符串
    if isinstance(value, list):                                                        # 列表按顺序递归序列化
        return "[" + ",".join(json_safe_string(x) for x in value) + "]"                # 拼成稳定列表字符串
    return str(value)                                                                  # 其他类型直接转字符串



if __name__ == "__main__":
    print("=" * 80)                                                                    # 分隔线，便于观察输出
    print("Identity 模块自测开始")                                                     # 提示测试开始
    print("=" * 80)                                                                    # 分隔线

    # ==================== 1) 默认实例测试 ====================
    idg = Identity()                                                                   # 创建默认身份生成器
    print("\n[1] 默认实例基础能力测试")                                                 # 输出测试标题
    print("性别:", idg.gender())                                                        # 测试性别生成
    print("中文姓名:", idg.fullName(locale="zh"))                                       # 测试中文姓名生成
    print("英文姓名:", idg.fullName(locale="en"))                                       # 测试英文姓名生成
    print("用户名:", idg.username())                                                    # 测试用户名生成
    print("密码:", idg.password())                                                      # 测试密码生成
    print("邮箱:", idg.email())                                                         # 测试邮箱生成
    print("中国手机号:", idg.phone(country="CN"))                                       # 测试中国手机号生成
    print("美国电话:", idg.phone(country="US"))                                         # 测试美国电话生成
    print("生日:", idg.birthDate())                                                     # 测试生日生成
    print("年龄:", idg.age())                                                           # 测试年龄生成
    print("中文地址:", idg.address(locale="zh"))                                        # 测试中文地址生成
    print("英文地址:", idg.address(locale="en"))                                        # 测试英文地址生成
    print("身份证风格编号(CN):", idg.nationalId(country="CN"))                          # 测试中国身份证风格编号生成
    print("护照风格编号(CN):", idg.passport(country="CN"))                              # 测试中国护照风格编号生成
    print("UUID 风格:", idg.uuidLike())                                                 # 测试 UUID 风格标识生成
    print("设备 ID:", idg.deviceId())                                                   # 测试设备 ID 生成
    print("浏览器资料:", idg.browserProfile())                                          # 测试浏览器资料生成
    print("公司信息:", idg.company())                                                   # 测试公司资料生成
    print("社交资料:", idg.socialProfile())                                             # 测试社交资料生成

    # ==================== 2) 简化身份测试 ====================
    print("\n[2] 简化身份档案测试")                                                     # 输出测试标题
    simple_zh = idg.simple(locale="zh")                                                # 生成中文简化身份
    simple_en = idg.simple(locale="en")                                                # 生成英文简化身份
    print("简化身份(zh):", simple_zh)                                                  # 输出中文简化身份
    print("简化身份(en):", simple_en)                                                  # 输出英文简化身份

    # ==================== 3) 完整身份测试 ====================
    print("\n[3] 完整身份档案测试")                                                     # 输出测试标题
    profile_mixed = idg.profile(locale="mixed")                                        # 生成 mixed 风格完整身份
    profile_zh = idg.profile(locale="zh", gender="female", country="CN")               # 生成中文女性完整身份
    profile_en = idg.profile(locale="en", gender="male", country="US")                 # 生成英文男性完整身份

    print("完整身份(mixed):")                                                          # 输出 mixed 档案标题
    for key, value in profile_mixed.items():                                           # 遍历完整档案字段
        print(f"  {key}: {value}")                                                     # 逐项输出字段和值

    print("\n完整身份(zh, female, CN):")                                               # 输出中文档案标题
    for key, value in profile_zh.items():                                              # 遍历中文档案字段
        print(f"  {key}: {value}")                                                     # 逐项输出

    print("\n完整身份(en, male, US):")                                                 # 输出英文档案标题
    for key, value in profile_en.items():                                              # 遍历英文档案字段
        print(f"  {key}: {value}")                                                     # 逐项输出

    # ==================== 4) 随机种子可复现测试 ====================
    print("\n[4] 随机种子可复现测试")                                                   # 输出测试标题
    seeded_a = Identity(seed=20250730, locale="en")                                    # 用固定种子创建第一个实例
    seeded_b = Identity(seed=20250730, locale="en")                                    # 用同样种子创建第二个实例

    profile_a = seeded_a.profile()                                                     # 生成第一份档案
    profile_b = seeded_b.profile()                                                     # 生成第二份档案

    print("profile_a email:", profile_a["email"])                                      # 输出第一份邮箱
    print("profile_b email:", profile_b["email"])                                      # 输出第二份邮箱
    print("profile_a username:", profile_a["username"])                                # 输出第一份用户名
    print("profile_b username:", profile_b["username"])                                # 输出第二份用户名
    print("是否可复现(email):", profile_a["email"] == profile_b["email"])              # 验证邮箱是否一致
    print("是否可复现(username):", profile_a["username"] == profile_b["username"])     # 验证用户名是否一致
    print("是否可复现(fingerprint):", profile_a["fingerprint"] == profile_b["fingerprint"])# 验证档案指纹是否一致

    # ==================== 5) 批量生成测试 ====================
    print("\n[5] 批量生成测试")                                                         # 输出测试标题
    batch = []                                                                         # 准备收集批量结果
    for i in range(5):                                                                 # 连续生成 5 个身份样本
        item = idg.simple(locale="mixed")                                              # 每次生成一个简化身份
        batch.append(item)                                                             # 加入结果列表
        print(f"样本 {i + 1}:", item)                                                  # 输出当前样本

    # ==================== 6) 指纹稳定性测试 ====================
    print("\n[6] 指纹稳定性测试")                                                       # 输出测试标题
    stable_source = {                                                                  # 构造稳定源对象
        "name": "Alice Smith",                                                         # 示例姓名
        "email": "alice@example.com",                                                  # 示例邮箱
        "phone": "+1-212-555-1234",                                                    # 示例电话
    }
    fp1 = idg.fingerprint(stable_source)                                               # 第一次生成指纹
    fp2 = idg.fingerprint(stable_source)                                               # 第二次生成指纹
    print("fingerprint #1:", fp1)                                                      # 输出第一次指纹
    print("fingerprint #2:", fp2)                                                      # 输出第二次指纹
    print("指纹是否稳定:", fp1 == fp2)                                                 # 验证同一输入是否得到同一指纹

    # ==================== 7) 表单填充模拟测试 ====================
    print("\n[7] 表单填充模拟测试")                                                     # 输出测试标题
    demo = idg.profile(locale="en")                                                    # 生成一份英文完整身份
    form_data = {                                                                      # 模拟注册表单字段映射
        "name": demo["full_name"],                                                     # 表单姓名
        "email": demo["email"],                                                        # 表单邮箱
        "username": demo["username"],                                                  # 表单用户名
        "password": demo["password"],                                                  # 表单密码
        "phone": demo["phone"],                                                        # 表单电话
        "address": demo["address"]["full"],                                            # 表单地址
        "device_id": demo["device_id"],                                                # 表单设备 ID
    }
    for key, value in form_data.items():                                               # 遍历表单字段
        print(f"  {key}: {value}")                                                     # 逐项输出

    print("\n" + "=" * 80)                                                             # 分隔线
    print("Identity 模块自测完成")                                                     # 提示测试结束
    print("=" * 80)                                                                    # 分隔线