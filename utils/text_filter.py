"""
纯文本过滤工具模块

提供台本页码/页眉页脚过滤等纯文本处理函数，供台本提取阶段调用。
不依赖项目其他模块（无循环依赖）。
"""

import re


def _is_page_number(text: str) -> bool:
    """判断文本是否为页码编号
    
    页码特征：
    1. 纯数字
    2. 数字+斜杠+数字（如 1/57）
    3. 短数字串（1-4位）
    """
    text = text.strip()
    if not text:
        return False
    
    # 纯数字（1-4位）
    if re.match(r'^\d{1,4}$', text):
        return True
    
    # 数字/数字格式（如 1/57, 2/100）
    if re.match(r'^\d{1,4}\s*/\s*\d{1,4}$', text):
        return True
    
    return False


def _is_header_footer(text: str) -> bool:
    """判断文本是否为页眉页脚内容
    
    常见页眉页脚特征：
    1. 包含"製糖"、"製作"等制作信息
    2. 包含作品名+数字
    """
    text = text.strip()
    if not text:
        return False
    
    # 制作信息
    if re.search(r'製糖|製作|発行|著作', text):
        return True
    
    return False


def _filter_page_numbers(text: str) -> str:
    """过滤文本中的页码和页眉页脚内容（兼容旧版）
    
    返回过滤后的文本
    """
    lines = text.split('\n')
    filtered_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # 跳过纯页码行
        if _is_page_number(stripped):
            continue
        
        # 跳过页眉页脚
        if _is_header_footer(stripped):
            continue
        
        # 跳过纯数字串（可能是页码残留）
        if re.match(r'^\d+$', stripped):
            continue
        
        # 跳过包含大量重复数字的行（如 22221111111111987654321）
        # 这些通常是PDF页面上的装饰性数字
        if re.search(r'(\d)\1{3,}', stripped):  # 4个以上连续相同数字
            # 但如果这行也包含日文，则保留
            japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', stripped))
            if japanese_chars == 0:
                continue
        
        # 跳过以长数字串开头的行
        if re.match(r'^\d{8,}', stripped):  # 8位以上数字开头
            # 提取数字后的内容
            match = re.search(r'^\d+\s*(.*)', stripped)
            if match and match.group(1).strip():
                # 保留数字后的内容
                filtered_lines.append(match.group(1).strip())
                continue
            else:
                continue
        
        filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)
