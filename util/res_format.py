import json



def merge_by_speaker(data, max_gap_seconds=2.0) -> list:
    """
    按说话人合并相邻的句子
    
    参数:
    data: 包含sentence_info的字典数据
    max_gap_seconds: 最大间隔时间（秒），超过此时间不合并
    
    返回:
    merged: 合并后的文本列表
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            data = eval(data)
    
    sentence_info = data.get("sentence_info", [])
    if not sentence_info:
        return []
    
    merged = []
    current_speaker = sentence_info[0].get("spk")
    current_text = sentence_info[0].get("text", "").strip()
    current_start = sentence_info[0].get("start", 0)
    current_end = sentence_info[0].get("end", 0)
    
    for i in range(1, len(sentence_info)):
        item = sentence_info[i]
        speaker = item.get("spk")
        text = item.get("text", "").strip()
        start_ms = item.get("start", 0)
        end_ms = item.get("end", 0)
        
        # 计算时间间隔
        gap_seconds = (start_ms - current_end) / 1000.0
        
        # 如果是同一个说话人且时间间隔不大，则合并
        if speaker == current_speaker and gap_seconds <= max_gap_seconds:
            current_text += text
            current_end = end_ms
        else:
            # 输出当前段落
            start_sec = current_start / 1000.0
            end_sec = current_end / 1000.0
            formatted_line = f"[{start_sec:.2f}s - {end_sec:.2f}s] Speaker {current_speaker}: {current_text}"
            merged.append(formatted_line)
            
            # 开始新段落
            current_speaker = speaker
            current_text = text
            current_start = start_ms
            current_end = end_ms
    
    # 添加最后一个段落
    start_sec = current_start / 1000.0
    end_sec = current_end / 1000.0
    formatted_line = f"[{start_sec:.2f}s - {end_sec:.2f}s] Speaker {current_speaker}: {current_text}"
    merged.append(formatted_line)
    
    return merged


def load_json(content:str) -> dict:
    """
    尝试将字符串内容解析为JSON对象。
    
    参数:
    content: 字符串内容
    
    返回:
    dict: 解析后的字典对象
    """
    try:
        data_list = eval(content)
        # 数据是一个列表，取第一个元素
        return data_list[0] if isinstance(data_list, list) else data_list
    except Exception as e:
        print(f"解析数据时出错: {e}")
        return {}