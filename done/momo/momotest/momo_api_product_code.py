# -*- coding: utf-8 -*-
"""
SCM_編碼查詢API規格文件_V2.1.docx
MOMO SCM Product API Client - Consolidated

This script provides a consolidated, all-in-one Python client for interacting
with the MOMO SCM Product APIs. It integrates the client logic, detailed
documentation, and practical examples for each API endpoint into a single class.
"""

import json
from typing import Dict, Any, List
import httpx

class MomoProductCodeAPI:
    """
    A consolidated client for all MOMO SCM Product API interactions.

    This client handles authentication, request signing, response decryption,
    and provides a method for each product-related API endpoint with detailed
    documentation and parameter examples included in the docstring.
    """

    def __init__(self, config_path: str = "config/momo_api_login.json", entp_code="011571"):
        """
        Initializes the MOMO API Client by loading credentials from a config file.

        Args:
            config_path (str): The path to the JSON configuration file.
        
        Raises:
            FileNotFoundError: If the config file is not found.
            ValueError: If the config file is missing required fields.
        """
        config = self._read_config(config_path, entp_code)

        self.entp_id = config["entpID"]
        self.entp_code = config["entpCode"]
        self.entp_pwd = config["entpPwd"]
        self.otp_back_no = config["otpBackNo"]
        self.base_url = config["scm_domain"]
        # self.api_path = "/GoodsServlet.do"
        
        # The decryption key is the master password padded to 16 bytes with '0'.
        self._decryption_key = self.entp_pwd.ljust(16, '0').encode('utf-8')[:16]

    def _read_config(self, config_path, entp_code):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Configuration file not found at '{config_path}'. "
                f"Please create it based on the documentation."
            )
        except json.JSONDecodeError:
            raise ValueError(f"Could not decode JSON from '{config_path}'. Please check its format.")

        required_keys = ["scm_domain", "entpID", "entpCode", "entpPwd", "otpBackNo"]

        # 兼容兩種格式：
        # 1) 平坦格式：{"scm_domain": "...", "entpID": "...", ...}
        # 2) 多帳號格式：{"011571": {"scm_domain": "...", ...}, "011572": {...}}
        if all(key in config for key in required_keys):
            return config

        if entp_code in config and all(key in config[entp_code] for key in required_keys):
            return config[entp_code]

        raise ValueError(f"Config file is missing one of the required keys: {required_keys}")
    
    # def _decrypt_response(self, encrypted_data: str) -> Dict[str, Any]:
    #     """
    #     Decrypts the AES-encrypted data from the API response.
    #     """
    #     try:
    #         encrypted_bytes = base64.b64decode(encrypted_data)
    #         cipher = Cipher(algorithms.AES(self._decryption_key), mode=modes.ECB())
    #         decryptor = cipher.decryptor()
    #         decrypted_padded = decryptor.update(encrypted_bytes) + decryptor.finalize()
    #         unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    #         unpadded_data = unpadder.update(decrypted_padded) + unpadder.finalize()
    #         decrypted_json_str = unpadded_data.decode('utf-8')
    #         return json.loads(decrypted_json_str)
        # except Exception as e:
        #     raise ValueError(f"Failed to decrypt response data: {e}") from e

    def _make_request(self, payload: Dict[str, Any], decrypt: bool = False, files: Dict = None, data: Dict = None) -> Dict[str, Any]:
        """
        Makes a POST request to the MOMO SCM API.
        """
        payload['loginInfo'] = {
            "entpID": self.entp_id,
            "entpCode": self.entp_code,
            "entpPwd": self.entp_pwd,
            "otpBackNo": self.otp_back_no
        }
        
        url = f"{self.base_url}{self.api_path}"
        headers = {}
        if not files: # Default to JSON if not a file upload
            headers['Content-Type'] = 'application/json'

        try:
            with httpx.Client() as client:
                if files:
                    #  For multipart/form-data, pass loginInfo in the 'data' part
                    json_data = json.loads(data['jsonValue'])
                    json_data['loginInfo'] = payload['loginInfo']
                    data['jsonValue'] = json.dumps(json_data)
                    response = client.post(url, data=data, files=files, headers=headers)
                    # response = client.post(url, data=json.dumps(payload), files=files, headers=headers)
                else:
                    response = client.post(url, json=payload, headers=headers)

                response.raise_for_status()
                response_json = response.json()

                # if decrypt and response_json.get("dataList"):
                #     decrypted_list = self._decrypt_response(response_json["dataList"])
                #     response_json["dataList"] = decrypted_list
                
                return response_json

        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP error occurred: {e.response.status_code} - {e.response.text}"}
        except httpx.RequestError as e:
            return {"error": f"An error occurred while requesting {e.request.url!r}: {e}"}
        except json.JSONDecodeError:
            return {"error": f"Failed to decode JSON response: {response.text}"}
        except ValueError as e:
            return {"error": str(e)}

    # --- API Functions ---
    def ecCategory(self, payload:Dict[str, str]) -> Dict[str, Any]:
        """(十三)、分類查詢
        用途與流程: 分類查詢。
    
        - ecCategoryName(str): 第四層分類名稱 - 模糊搜索
        - ecCategoryCode(str): 第四層分類代碼 - 填寫完整
        兩項擇一填寫即可，若兩項都有填的情況下會以分類代碼作查詢

        payload_example = {
            "ecCategoryName": "", "ecCategoryCode": ""
        }
        """#{SCM_domain}/api/v1/goods/basic_code/ecCategory/D1102.scm
        self.api_path = "/api/v1/goods/basic_code/ecCategory/D1102.scm"
        
        return self._make_request(payload)

    def ecIndex(self, payload:Dict[str, str]) -> Dict[str, Any]:
        """(十四)、分類屬性查詢
        用途與流程: 分類屬性查詢。
        - ecCategoryCode(str): 末端分類代碼

        payload_example = {
            "ecCategoryCode": ""
        }
        """
        self.api_path = "/api/v1/goods/basic_code/ecIndex/D1102.scm"
        return self._make_request(payload)