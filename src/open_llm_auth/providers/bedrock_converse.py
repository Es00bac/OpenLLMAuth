from __future__ import annotations

import configparser
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

import httpx

from .base import BaseProvider


@dataclass
class AwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str] = None


class BedrockConverseProvider(BaseProvider):
    """
    Amazon Bedrock Converse adapter with SigV4 signing.
    """

    service_name = "bedrock"

    async def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        region = self._resolve_region()
        body = self._build_converse_request_body(messages, payload)
        endpoint = self._converse_endpoint(model, stream=False)
        data = await self._signed_post_json(endpoint, body, region=region)
        return self._convert_converse_response(data, fallback_model=model)

    async def chat_completion_stream(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        # Bedrock's converse-stream uses AWS eventstream. To preserve OpenAI streaming
        # compatibility without adding an eventstream parser dependency, we execute a
        # signed converse call and emit a compliant one-shot SSE response.
        result = await self.chat_completion(model=model, messages=messages, payload=payload)
        choice = result.get("choices", [{}])[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = message.get("content", "")
        finish_reason = choice.get("finish_reason", "stop") if isinstance(choice, dict) else "stop"
        model_name = str(result.get("model") or model)
        created = int(result.get("created") or int(time.time()))
        chunk_id = str(result.get("id") or f"chatcmpl-bedrock-{created}")

        async def _stream() -> AsyncIterator[bytes]:
            role_chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(role_chunk, ensure_ascii=True)}\n\n".encode("utf-8")

            if content:
                text_chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(text_chunk, ensure_ascii=True)}\n\n".encode("utf-8")

            done_chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
            yield f"data: {json.dumps(done_chunk, ensure_ascii=True)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return _stream()

    async def list_models(self) -> List[Dict[str, Any]]:
        # Model enumeration for Bedrock is account/permission specific and usually
        # requires AWS Control Plane APIs (different endpoint/permissions). Keep
        # model listing empty unless configured via catalog/models config.
        return []

    def _build_converse_request_body(
        self, messages: List[Dict[str, Any]], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        system_chunks: List[Dict[str, str]] = []
        bedrock_messages: List[Dict[str, Any]] = []

        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower()
            text = self._extract_text(msg.get("content"))
            if not text:
                continue

            if role == "system":
                system_chunks.append({"text": text})
                continue

            if role == "tool":
                role = "user"
                text = f"[tool_result] {text}"
            if role not in {"user", "assistant"}:
                role = "user"

            bedrock_messages.append({"role": role, "content": [{"text": text}]})

        body: Dict[str, Any] = {"messages": bedrock_messages or [{"role": "user", "content": [{"text": ""}]}]}
        if system_chunks:
            body["system"] = system_chunks

        inference: Dict[str, Any] = {}
        max_tokens = payload.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            inference["maxTokens"] = max_tokens
        temperature = payload.get("temperature")
        if isinstance(temperature, (int, float)):
            inference["temperature"] = float(temperature)
        top_p = payload.get("top_p")
        if isinstance(top_p, (int, float)):
            inference["topP"] = float(top_p)
        stop = payload.get("stop")
        if isinstance(stop, str) and stop:
            inference["stopSequences"] = [stop]
        elif isinstance(stop, list):
            stop_sequences = [str(item) for item in stop if isinstance(item, str) and item]
            if stop_sequences:
                inference["stopSequences"] = stop_sequences
        if inference:
            body["inferenceConfig"] = inference

        return body

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunk for chunk in chunks if chunk).strip()
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text
        return ""

    def _converse_endpoint(self, model: str, *, stream: bool) -> str:
        action = "converse-stream" if stream else "converse"
        model_segment = quote(model, safe="-_.~")
        return f"{self.base_url}/model/{model_segment}/{action}"

    async def _signed_post_json(self, url: str, body: Dict[str, Any], *, region: str) -> Dict[str, Any]:
        payload_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = self._build_request_headers(url, payload_bytes, region=region)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, content=payload_bytes, headers=headers)
            response.raise_for_status()
            return response.json()

    def _build_request_headers(self, url: str, payload_bytes: bytes, *, region: str) -> Dict[str, str]:
        base_headers = {k: v for k, v in self.headers.items()}
        base_headers.setdefault("Content-Type", "application/json")
        base_headers.setdefault("Accept", "application/json")

        bearer = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "").strip()
        if bearer:
            base_headers["Authorization"] = f"Bearer {bearer}"
            return base_headers

        creds = self._resolve_aws_credentials()
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        datestamp = amz_date[:8]
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        signed_headers, canonical_headers = self._canonical_headers(
            url=url,
            amz_date=amz_date,
            payload_hash=payload_hash,
            session_token=creds.session_token,
            extra_headers=base_headers,
        )
        canonical_request = "\n".join(
            [
                "POST",
                self._canonical_uri(url),
                self._canonical_query_string(url),
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{datestamp}/{region}/{self.service_name}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = self._signature_key(creds.secret_access_key, datestamp, region, self.service_name)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        signed = dict(base_headers)
        signed["x-amz-date"] = amz_date
        signed["x-amz-content-sha256"] = payload_hash
        if creds.session_token:
            signed["x-amz-security-token"] = creds.session_token
        signed["Authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={creds.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        return signed

    @staticmethod
    def _canonical_headers(
        *,
        url: str,
        amz_date: str,
        payload_hash: str,
        session_token: Optional[str],
        extra_headers: Dict[str, str],
    ) -> Tuple[str, str]:
        host = urlsplit(url).netloc
        headers: Dict[str, str] = {"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash}
        for key, value in extra_headers.items():
            if value is None:
                continue
            headers[key.strip().lower()] = " ".join(str(value).strip().split())
        if session_token:
            headers["x-amz-security-token"] = session_token

        sorted_items = sorted(headers.items(), key=lambda item: item[0])
        signed_headers = ";".join(k for k, _ in sorted_items)
        canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted_items)
        return signed_headers, canonical_headers

    @staticmethod
    def _canonical_uri(url: str) -> str:
        path = urlsplit(url).path or "/"
        # Path is already encoded by _converse_endpoint; keep slashes.
        return re.sub(r"/{2,}", "/", path)

    @staticmethod
    def _canonical_query_string(url: str) -> str:
        query = urlsplit(url).query
        if not query:
            return ""
        pairs = []
        for item in query.split("&"):
            if "=" in item:
                k, v = item.split("=", 1)
            else:
                k, v = item, ""
            pairs.append((quote(k, safe="-_.~"), quote(v, safe="-_.~")))
        pairs.sort()
        return "&".join(f"{k}={v}" for k, v in pairs)

    @staticmethod
    def _signature_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
        k_date = hmac.new(f"AWS4{secret_key}".encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
        k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
        k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
        return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()

    def _resolve_region(self) -> str:
        env_region = os.getenv("AWS_REGION", "").strip() or os.getenv("AWS_DEFAULT_REGION", "").strip()
        if env_region:
            return env_region

        host = urlsplit(self.base_url).hostname or ""
        match = re.search(r"bedrock-runtime(?:-fips)?\.([a-z0-9-]+)\.amazonaws\.com", host)
        if match:
            return match.group(1)

        profile = os.getenv("AWS_PROFILE", "").strip() or "default"
        region = self._load_region_from_aws_config(profile)
        return region or "us-east-1"

    @staticmethod
    def _load_region_from_aws_config(profile: str) -> Optional[str]:
        config_path = Path.home() / ".aws" / "config"
        if not config_path.exists():
            return None
        parser = configparser.RawConfigParser()
        parser.read(config_path)
        sections = [profile, f"profile {profile}"] if profile != "default" else ["default"]
        for section in sections:
            if parser.has_option(section, "region"):
                value = parser.get(section, "region").strip()
                if value:
                    return value
        return None

    @staticmethod
    def _resolve_aws_credentials() -> AwsCredentials:
        access = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        token = os.getenv("AWS_SESSION_TOKEN", "").strip() or None
        if access and secret:
            return AwsCredentials(access_key_id=access, secret_access_key=secret, session_token=token)

        profile = os.getenv("AWS_PROFILE", "").strip() or "default"
        creds = BedrockConverseProvider._load_credentials_from_files(profile)
        if creds:
            return creds

        raise ValueError(
            "AWS credentials not found for Bedrock. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or configure ~/.aws/credentials (and optional AWS_PROFILE)."
        )

    @staticmethod
    def _load_credentials_from_files(profile: str) -> Optional[AwsCredentials]:
        credentials_path = Path.home() / ".aws" / "credentials"
        config_path = Path.home() / ".aws" / "config"

        parser = configparser.RawConfigParser()
        read_paths = []
        if credentials_path.exists():
            read_paths.append(str(credentials_path))
        if config_path.exists():
            read_paths.append(str(config_path))
        if not read_paths:
            return None

        parser.read(read_paths)
        sections = [profile, f"profile {profile}"] if profile != "default" else ["default"]
        for section in sections:
            if not parser.has_section(section):
                continue
            access = parser.get(section, "aws_access_key_id", fallback="").strip()
            secret = parser.get(section, "aws_secret_access_key", fallback="").strip()
            session = parser.get(section, "aws_session_token", fallback="").strip() or None
            if access and secret:
                return AwsCredentials(access_key_id=access, secret_access_key=secret, session_token=session)
        return None

    @staticmethod
    def _convert_converse_response(data: Dict[str, Any], fallback_model: str) -> Dict[str, Any]:
        message = ((data.get("output") or {}).get("message") or {}) if isinstance(data, dict) else {}
        content_items = message.get("content") if isinstance(message, dict) else []
        texts: List[str] = []
        for item in content_items or []:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        content = "".join(texts)

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("inputTokens") or 0)
        completion_tokens = int(usage.get("outputTokens") or 0)
        total_tokens = int(usage.get("totalTokens") or (prompt_tokens + completion_tokens))

        stop_reason = str(data.get("stopReason") or "end_turn").strip().lower()
        finish_reason = "length" if stop_reason == "max_tokens" else "stop"

        return {
            "id": f"chatcmpl-bedrock-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(data.get("modelId") or fallback_model),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
