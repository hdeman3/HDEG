"""
台本识别记录脚本
扫描testFile目录，识别台本文件并保存记录
"""

from scriptbook_utils import is_scriptbook_file, detect_scriptbook_language, extract_pdf_text, clean_script_for_translation
from pathlib import Path
import json

def main():
    test_dir = Path('testFile')
    records = []

    # 遍历所有文件
    for fpath in test_dir.rglob('*'):
        if fpath.is_file() and fpath.suffix.lower() in {'.txt', '.pdf'}:
            record = {
                'file': str(fpath),
                'is_scriptbook': is_scriptbook_file(fpath),
            }
            
            # 如果是PDF，提取文本（不管是否是台本）
            if fpath.suffix.lower() == '.pdf':
                # 先提取原始文本
                raw_text = extract_pdf_text(fpath, clean_for_translation=False)
                if raw_text:
                    record['pdf_extracted'] = True
                    record['raw_char_count'] = len(raw_text)
                    
                    # 再清洗文本，只保留对话
                    cleaned_text = extract_pdf_text(fpath, clean_for_translation=True)
                    
                    # 如果清洗后为空，使用原始文本
                    final_text = cleaned_text if cleaned_text else raw_text
                    record['char_count'] = len(final_text)
                    record['cleaned_char_count'] = len(cleaned_text) if cleaned_text else 0
                    record['preview'] = final_text[:500]
                    
                    # 保存文本到对应的txt文件
                    txt_path = fpath.with_suffix('.extracted.txt')
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(final_text)
                    record['saved_to'] = str(txt_path)
                else:
                    record['pdf_extracted'] = False
                    record['error'] = '提取失败'
            
            # 如果是txt，读取内容
            elif fpath.suffix.lower() == '.txt':
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    record['char_count'] = len(content)
                    record['preview'] = content[:500]
                except Exception as e:
                    record['error'] = str(e)
            
            records.append(record)

    # 保存总记录
    record_file = test_dir / '.scriptbook_records.json'
    with open(record_file, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    scriptbook_count = sum(1 for r in records if r['is_scriptbook'])
    print(f'已保存记录到: {record_file}')
    print(f'共识别 {scriptbook_count} 个台本文件')

    # 打印详情
    for r in records:
        if r['is_scriptbook']:
            print(f'\n文件: {r["file"]}')
            print(f'  语言: {r.get("language", "unknown")}')
            if 'char_count' in r:
                print(f'  字符数: {r["char_count"]}')
            if 'saved_to' in r:
                print(f'  已保存到: {r["saved_to"]}')

if __name__ == '__main__':
    main()
