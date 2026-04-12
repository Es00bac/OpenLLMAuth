from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List

import httpx

from .base import BaseProvider, compact_payload


class OpenAIProvider(BaseProvider):
    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = compact_payload({**payload, "model": model, "messages": messages})

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self.headers,
            )
            response.raise_for_status()
            response_data = response.json()
            if "choices" in response_data and len(response_data["choices"]) > 0:
                message = response_data["choices"][0].get("message", {})
                content = message.get("content", "")
                tool_calls = message.get("tool_calls", [])
                
                if not tool_calls and content:
                    import re
                    import json
                    
                    import time
                    
                    syn_tool_calls = []

                    print(f"[DEBUG] openai_provider content: {repr(content)}")
                    
                    # 1. Match XML <tool>...</tool><args>...</args>
                    xml_match = re.search(r"<tool>(.*?)<\/tool>.*?<args>(.*?)<\/args>", content, flags=re.DOTALL)
                    # 2. Match <invoke name="..."><parameter>...</parameter><content>...</content>
                    invoke_match = re.search(r'<invoke name="([^"]+)">.*?<parameter[^>]*>(.*?)<\/parameter>.*?<content>(.*?)<\/content>', content, flags=re.DOTALL)
                    # 3. Match generic JSON blocks with tool_calls
                    json_match = re.search(r'```(?:json)?\s*(\{.*?"tool_calls".*?\})\s*```', content, flags=re.DOTALL)
                    # 4. Match raw bash/sh blocks
                    bash_match = re.search(r'```(?:bash|sh)\s*(.*?)\s*```', content, flags=re.DOTALL)
                    
                    print(f"[DEBUG] openai match status -> xml:{bool(xml_match)} invoke:{bool(invoke_match)} json:{bool(json_match)} bash:{bool(bash_match)}")
                    
                    if invoke_match:
                        import time
                        file_path = invoke_match.group(2).strip()
                        file_content = invoke_match.group(3)
                        syn_tool_calls.append({
                            "id": f"call_{int(time.time() * 1000)}",
                            "type": "function",
                            "function": {
                                "name": "fs_write_file" if invoke_match.group(1) == "write_file" else invoke_match.group(1),
                                "arguments": json.dumps({"filePath": file_path, "content": file_content})
                            }
                        })
                    elif xml_match:
                        import time
                        args_str = xml_match.group(2).strip()
                        file_match = re.search(r"<file>.*?<path>(.*?)<\/path>.*?<content>(.*?)<\/content>.*?<\/file>", args_str, flags=re.DOTALL)
                        if file_match:
                            args_str = json.dumps({"filePath": file_match.group(1).strip(), "content": file_match.group(2)})
                            
                        syn_tool_calls.append({
                            "id": f"call_{int(time.time() * 1000)}",
                            "type": "function",
                            "function": {
                                "name": xml_match.group(1).strip(),
                                "arguments": args_str
                            }
                        })
                    elif json_match:
                        try:
                            parsed = json.loads(json_match.group(1))
                            if "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
                                import time
                                for tc in parsed["tool_calls"]:
                                    func = tc.get("function", {})
                                    func_name = func.get("name")
                                    args_val = func.get("arguments")
                                    
                                    if func_name in ("write_file", "create_file"):
                                        func_name = "fs_write_file"
                                        
                                    if isinstance(args_val, dict):
                                        args_val = json.dumps({
                                            "filePath": args_val.get("path", args_val.get("filePath", args_val.get("file_path"))),
                                            "content": args_val.get("content")
                                        })
                                    elif not isinstance(args_val, str):
                                        args_val = json.dumps(args_val)
                                        
                                    syn_tool_calls.append({
                                        "id": tc.get("id", f"call_{int(time.time() * 1000)}"),
                                        "type": "function",
                                        "function": {
                                            "name": func_name,
                                            "arguments": args_val
                                        }
                                    })
                        except Exception:
                            pass
                    elif bash_match:
                        import time
                        syn_tool_calls.append({
                            "id": f"call_{int(time.time() * 1000)}",
                            "type": "function",
                            "function": {
                                "name": "bash_run_command",
                                "arguments": json.dumps({"command": bash_match.group(1).strip()})
                            }
                        })
                        
                    if syn_tool_calls:
                        response_data["choices"][0]["message"]["tool_calls"] = syn_tool_calls
            
            return self.attach_response_telemetry(
                response_data,
                headers=response.headers,
                endpoint="chat.completions",
            )

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        body = compact_payload({**payload, "model": model, "messages": messages, "stream": True})
        headers = {**self.headers}
        headers.setdefault("Accept", "text/event-stream")

        async def _stream() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk

        return _stream()

    async def list_models(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=min(self.timeout, 20.0)) as client:
            response = await client.get(f"{self.base_url}/models", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                models = data.get("data")
                if isinstance(models, list):
                    return [m for m in models if isinstance(m, dict)]
            return []

    async def embeddings(
        self,
        *,
        model: str,
        input_texts: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = compact_payload({**payload, "model": model, "input": input_texts})

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                json=body,
                headers=self.headers,
            )
            response.raise_for_status()
            return self.attach_response_telemetry(
                response.json(),
                headers=response.headers,
                endpoint="embeddings",
            )

    async def get_usage_telemetry(self, days: int = 7) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": self.provider_id,
            "window_days": max(1, int(days)),
            "kind": "provider_account",
            "supported_fields": {
                "live_rate_limits": True,
                "account_usage": False,
                "billing_cycle": False,
                "subscription_cost": False,
            },
            "note": "OpenAI-compatible adapters expose live rate-limit headers, but account billing and subscription state are not queried by this adapter.",
        }
