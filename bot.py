import time
import json
import yaml
import hashlib
import telegram
import requests
import smtplib
import subprocess
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime
from pyrate_limiter import Duration, Rate, Limiter

from utils import *

__all__ = ["feishuBot", "wecomBot", "dingtalkBot", "qqBot", "telegramBot", "mailBot"]
today = datetime.now().strftime("%Y-%m-%d")


class BaseTranslator:
    """百度翻译基类"""
    
    def __init__(self, appid, key, from_lang='en', to_lang='zh'):
        self.appid = appid
        self.key = key
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.api_url = 'https://fanyi-api.baidu.com/api/trans/vip/translate'
    
    def translate_batch(self, texts: list) -> dict:
        """批量翻译文本，返回 {原文：译文} 字典"""
        if not texts:
            return {}
        
        # 过滤掉空文本和纯中文文本
        texts_to_translate = []
        import re
        for text in texts:
            if not text:
                continue
            has_english = bool(re.search(r'[a-zA-Z]', text))
            has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))
            # 只翻译包含英文且不包含中文的文本
            if has_english and not has_chinese:
                texts_to_translate.append(text)
        
        if not texts_to_translate:
            return {}
        
        # 用换行符连接所有文本（百度翻译支持批量翻译）
        joined_text = '\n'.join(texts_to_translate)
        
        # 生成随机盐
        salt = str(hashlib.md5(str(time.time()).encode()).hexdigest()[:10])
        
        # 生成签名
        sign_str = self.appid + joined_text + salt + self.key
        sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
        
        # 拼接请求参数
        params = {
            'q': joined_text,
            'from': self.from_lang,
            'to': self.to_lang,
            'appid': self.appid,
            'salt': salt,
            'sign': sign
        }
        
        try:
            response = requests.get(self.api_url, params=params, timeout=10)
            result = response.json()
            
            if 'trans_result' in result:
                # 返回翻译结果字典
                translations = {}
                for item in result['trans_result']:
                    src = item['src']
                    dst = item['dst']
                    translations[src] = dst
                return translations
            elif 'error_code' in result:
                print(f'[-] 批量翻译失败：{result["error_msg"]} (code: {result["error_code"]})')
                return {}
            else:
                return {}
        except Exception as e:
            print(f'[-] 批量翻译请求异常：{e}')
            return {}
    
    def translate(self, text):
        """翻译单个文本（兼容旧接口）"""
        if not text:
            return text
        
        result = self.translate_batch([text])
        return result.get(text, text)


class feishuBot:
    """飞书群机器人
    https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
    """

    def __init__(self, key, proxy_url='') -> None:
        self.key = key
        self.proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {
            'http': None, 'https': None}

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f'[ {feed} ]\n\n'
            for title, link in value.items():
                text += f'{title}\n{link}\n\n'
            text_list.append(text.strip())
        return text_list

    async def send(self, text_list: list):
        for text in text_list:
            print(f'{len(text)} {text[:50]}...{text[-50:]}')

            data = {"msg_type": "text", "content": {"text": text}}
            headers = {'Content-Type': 'application/json'}
            url = f'https://open.feishu.cn/open-apis/bot/v2/hook/{self.key}'
            r = requests.post(url=url, headers=headers,
                              data=json.dumps(data), proxies=self.proxy)

            if r.status_code == 200:
                console.print('[+] feishuBot 发送成功', style='bold green')
            else:
                console.print('[-] feishuBot 发送失败', style='bold red')
                print(r.text)

    async def send_markdown(self, text):
        # TODO 富文本
        data = {"msg_type": "text", "content": {"text": text}}
        self.send(data)


class wecomBot:
    """企业微信群机器人
    https://developer.work.weixin.qq.com/document/path/91770
    """

    def __init__(self, key, proxy_url='', translator=None) -> None:
        self.key = key
        self.proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {
            'http': None, 'https': None}
        self.max_bytes = 4096  # 企业微信 markdown 消息最大字节数
        self.translator = translator  # 翻译器实例

    @staticmethod
    def parse_results(results: list):
        """将所有 RSS 内容合并为一个列表，每个元素为 (feed, items) 元组"""
        text_list = []
        for result in results:
            (feed, value), = result.items()
            items = [(title, link) for title, link in value.items()]
            text_list.append((feed, items))
        return text_list

    def _collect_english_titles(self, text_list: list) -> list:
        """收集所有需要翻译的英文标题"""
        import re
        titles = []
        for feed, items in text_list:
            for title, link in items:
                has_english = bool(re.search(r'[a-zA-Z]', title))
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', title))
                if has_english and not has_chinese:
                    titles.append(title)
        return titles

    def _split_messages(self, text_list: list, translations: dict = None) -> list:
        """根据 4096 字节限制分割消息内容"""
        translations = translations or {}
        messages = []
        current_text = ""
        
        for feed, items in text_list:
            # 添加 feed 标题
            feed_header = f'## {feed}\n'
            
            for title, link in items:
                # 使用翻译后的标题（如果有）
                translated_title = translations.get(title, title)
                # 如果翻译了，显示原文和译文（使用括号包裹译文，避免 markdown 链接换行问题）
                if translated_title != title:
                    item_text = f'- [{title} ({translated_title})]({link})\n'
                else:
                    item_text = f'- [{translated_title}]({link})\n'
                
                # 计算当前内容加上新的 item 后的字节长度
                test_text = current_text + feed_header + item_text
                byte_length = len(test_text.encode('utf-8'))
                
                # 如果加上新 item 后超过限制
                if byte_length > self.max_bytes:
                    # 如果当前已有内容，先发送当前内容
                    if current_text.strip():
                        messages.append(current_text.strip())
                        current_text = feed_header + item_text
                    else:
                        # 如果当前为空但单个 item 就超限，则截断
                        test_with_header = feed_header + item_text
                        if len(test_with_header.encode('utf-8')) > self.max_bytes:
                            # 截断到限制内
                            truncated = self._truncate_to_limit(feed_header + item_text, self.max_bytes)
                            messages.append(truncated.strip())
                            current_text = ""
                        else:
                            current_text = test_with_header
                else:
                    # 如果当前内容为空，先添加 feed 标题
                    if not current_text:
                        current_text = feed_header + item_text
                    else:
                        current_text += item_text
        
        # 添加最后剩余的内容
        if current_text.strip():
            messages.append(current_text.strip())
        
        return messages

    def _truncate_to_limit(self, text: str, max_bytes: int) -> str:
        """将文本截断到指定字节限制内"""
        encoded = text.encode('utf-8')
        if len(encoded) <= max_bytes:
            return text
        # 截断并尝试在字符边界处切割
        truncated = encoded[:max_bytes].decode('utf-8', errors='ignore')
        return truncated

    async def send(self, text_list: list):
        limiter = Limiter([Rate(20, Duration.MINUTE)])  # 频率限制，20条/分钟

        # 先收集所有英文标题并批量翻译
        translations = {}
        if self.translator:
            english_titles = self._collect_english_titles(text_list)
            if english_titles:
                console.print(f'[+] 正在批量翻译 {len(english_titles)} 个英文标题...', style='bold yellow')
                translations = self.translator.translate_batch(english_titles)
                if translations:
                    console.print(f'[+] 翻译完成：{len(translations)} 个标题', style='bold green')
        
        # 根据字节限制分割消息（使用翻译结果）
        messages = self._split_messages(text_list, translations)
        
        for text in messages:
            limiter.try_acquire('identity')
            print(f'{len(text.encode("utf-8"))} {text[:50]}...{text[-50:]}')

            data = {"msgtype": "markdown", "markdown": {"content": text}}
            headers = {'Content-Type': 'application/json'}
            url = f'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.key}'
            r = requests.post(url=url, headers=headers, data=json.dumps(data), proxies=self.proxy)

            if r.status_code == 200:
                console.print('[+] wecomBot 发送成功', style='bold green')
            else:
                console.print('[-] wecomBot 发送失败', style='bold red')
                print(r.text)




class dingtalkBot:
    """钉钉群机器人
    https://open.dingtalk.com/document/robots/custom-robot-access
    """

    def __init__(self, key, proxy_url='') -> None:
        self.key = key
        self.proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {
            'http': None, 'https': None}

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = ''.join(
                f'- [{title}]({link})\n' for title, link in value.items())
            text_list.append([feed, text.strip()])
        return text_list

    async def send(self, text_list: list):
        limiter = Limiter([Rate(20, Duration.MINUTE)])  # 频率限制，20条/分钟

        for (feed, text) in text_list:
            limiter.try_acquire('identity')

            text = f'## {feed}\n{text}'
            text += f"\n\n <!-- Powered by Yarb. -->"
            print(f'{len(text)} {text[:50]}...{text[-50:]}')

            data = {"msgtype": "markdown", "markdown": {
                "title": feed, "text": text}}
            headers = {'Content-Type': 'application/json'}
            url = f'https://oapi.dingtalk.com/robot/send?access_token={self.key}'
            r = requests.post(url=url, headers=headers,
                                data=json.dumps(data), proxies=self.proxy)

            if r.status_code == 200:
                console.print('[+] dingtalkBot 发送成功', style='bold green')
            else:
                console.print('[-] dingtalkBot 发送失败', style='bold red')
                print(r.text)


class qqBot:
    """QQ群机器人
    https://github.com/Mrs4s/go-cqhttp
    """
    cqhttp_path = Path(__file__).absolute().parent.joinpath('cqhttp')

    def __init__(self, group_id: list) -> None:
        self.server = 'http://127.0.0.1:5700'
        self.group_id = group_id

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f'[ {feed} ]\n\n'
            for title, link in value.items():
                text += f'{title}\n{link}\n\n'
            text_list.append(text.strip())
        return text_list

    async def send(self, text_list: list):
        limiter = Limiter([Rate(20, Duration.MINUTE)])  # 频率限制，20条/分钟

        for text in text_list:
            limiter.try_acquire('identity')
            print(f'{len(text)} {text[:50]}...{text[-50:]}')

            for id in self.group_id:
                try:
                    r = requests.post(f'{self.server}/send_group_msg?group_id={id}&&message={text}')
                    if r.status_code == 200:
                        console.print(f'[+] qqBot 发送成功 {id}', style='bold green')
                    else:
                        console.print(f'[-] qqBot 发送失败 {id}', style='bold red')
                except Exception as e:
                    console.print(f'[-] qqBot 发送失败 {id}', style='bold red')
                    print(e)

    async def start_server(self, qq_id, qq_passwd, timeout=60):
        config_path = self.cqhttp_path.joinpath('config.yml')
        with open(config_path, 'r') as f:
            data = yaml.load(f, Loader=yaml.FullLoader)
            data['account']['uin'] = int(qq_id)
            data['account']['password'] = qq_passwd
        with open(config_path, 'w+') as f:
            yaml.dump(data, f)

        subprocess.run('cd cqhttp && ./go-cqhttp -d', shell=True)

        timeout = time.time() + timeout
        while True:
            try:
                requests.get(self.server)
                console.print('[+] qqBot 启动成功', style='bold green')
                return True
            except Exception as e:
                time.sleep(1)

            if time.time() > timeout:
                qqBot.kill_server()
                console.print('[-] qqBot 启动失败', style='bold red')
                return False

    @classmethod
    def kill_server(cls):
        pid_path = cls.cqhttp_path.joinpath('go-cqhttp.pid')
        subprocess.run(f'cat {pid_path} | xargs kill',
                       stderr=subprocess.DEVNULL, shell=True)


class mailBot:
    """邮件机器人
    """

    def __init__(self, sender, passwd, receiver: str, fromwho='', server='') -> None:
        self.sender = sender
        self.receiver = receiver
        self.fromwho = fromwho or sender
        server = server or self.get_server(sender)

        self.smtp = smtplib.SMTP_SSL(server)
        self.smtp.login(sender, passwd)

    def get_server(self, sender: str):
        key = sender.rstrip('.com').split('@')[-1]
        server = {
            'qq': 'smtp.qq.com',
            'foxmail': 'smtp.qq.com',
            '163': 'smtp.163.com',
            'sina': 'smtp.sina.com',
            'gmail': 'smtp.gmail.com',
            'outlook': 'smtp.live.com',
        }
        return server.get(key, f'smtp.{key}.com')

    @staticmethod
    def parse_results(results: list):
        text = f'<html><head><h1>每日安全资讯（{today}）</h1></head><body>'
        for result in results:
            (feed, value), = result.items()
            text += f'<h3>{feed}</h3><ul>'
            for title, link in value.items():
                text += f'<li><a href="{link}">{title}</a></li>'
            text += '</ul>'
        text += '<br><br><b>如不需要，可直接回复本邮件退订。</b></body></html>'
        print(text)
        return text

    async def send(self, text: str):
        print(f'{len(text)} {text[:50]}...{text[-50:]}')

        msg = MIMEText(text, 'html')
        msg['Subject'] = Header(f'每日安全资讯（{today}）')
        msg['From'] = self.fromwho
        msg['To'] = self.receiver

        try:
            self.smtp.sendmail(
                self.sender, self.receiver.split(','), msg.as_string())
            console.print('[+] mailBot 发送成功', style='bold green')
        except Exception as e:
            console.print('[+] mailBot 发送失败', style='bold red')
            print(e)


class telegramBot:
    """Telegram机器人
    https://core.telegram.org/bots/api
    """

    def __init__(self, key, chat_id: list, proxy_url='') -> None:
        self.key = key
        self.proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {
            'http': None, 'https': None}

        proxy = telegram.request.HTTPXRequest(proxy_url=None)
        self.chat_id = chat_id
        self.bot = telegram.Bot(token=key, request=proxy)

    async def test_connect(self):
        try:
            await self.bot.get_me()
            return True
        except Exception as e:
            console.print('[-] telegramBot 连接失败', style='bold red')
            return False

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f'<b>{feed}</b>\n'
            for idx, (title, link) in enumerate(value.items()):
                text += f'{idx+1}. <a href="{link}">{title}</a>\n'
            text_list.append(text.strip())
        return text_list

    async def send(self, text_list: list):
        limiter = Limiter([Rate(20, Duration.MINUTE)])  # 频率限制，20条/分钟

        for text in text_list:
            limiter.try_acquire('identity')
            print(f'{len(text)} {text[:50]}...{text[-50:]}')

            for id in self.chat_id:
                try:
                    self.bot.send_message(chat_id=id, text=text, parse_mode='HTML')
                    console.print(f'[+] telegramBot 发送成功 {id}', style='bold green')
                except Exception as e:
                    console.print(f'[-] telegramBot 发送失败 {id}', style='bold red')
                    print(e)
