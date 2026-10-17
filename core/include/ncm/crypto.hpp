#pragma once

#include <cstdint>
#include <string>

namespace ncm {

std::string md5_hex(const std::string& data);
std::string aes128_ecb_encrypt_hex(const std::string& plain, const std::string& key);
std::string base64_encode(const unsigned char* data, std::size_t len);
std::string eapi_encrypt_params(const std::string& url, const std::string& payload_json);
std::string encrypted_pic_id(const std::string& pic_id);
std::string pic_url(std::int64_t pic_id, int size);
std::string url_encode(const std::string& value);

std::string encrypt_secret(const std::string& password, const std::string& plaintext);
std::string decrypt_secret(const std::string& password, const std::string& blob);

}
