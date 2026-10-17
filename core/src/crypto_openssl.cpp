#include "ncm/crypto.hpp"

#include <openssl/evp.h>
#include <openssl/rand.h>

#include <cstring>
#include <stdexcept>
#include <vector>

namespace ncm {
namespace {

const char* kHexDigits = "0123456789abcdef";
const char* kPicMagic = "3go8&$8*3*3h0k(2)2";

std::vector<unsigned char> digest(const EVP_MD* algorithm,
                                  const std::string& data) {
  unsigned int length = 0;
  std::vector<unsigned char> result(EVP_MAX_MD_SIZE);
  if (EVP_Digest(data.empty() ? "" : data.data(), data.size(), result.data(),
                 &length, algorithm, nullptr) != 1) {
    throw std::runtime_error("failed to compute digest");
  }
  result.resize(length);
  return result;
}

std::string to_hex(const std::vector<unsigned char>& data) {
  std::string out;
  out.reserve(data.size() * 2);
  for (unsigned char byte : data) {
    out.push_back(kHexDigits[byte >> 4]);
    out.push_back(kHexDigits[byte & 0x0F]);
  }
  return out;
}

std::vector<unsigned char> pbkdf2_sha256(const std::string& password,
                                         const std::vector<unsigned char>& salt,
                                         unsigned long long iterations,
                                         unsigned long long length) {
  std::vector<unsigned char> out(static_cast<std::size_t>(length));
  if (PKCS5_PBKDF2_HMAC(password.data(),
                        static_cast<int>(password.size()), salt.data(),
                        static_cast<int>(salt.size()),
                        static_cast<int>(iterations), EVP_sha256(),
                        static_cast<int>(out.size()), out.data()) != 1) {
    throw std::runtime_error("failed to derive key");
  }
  return out;
}

std::vector<unsigned char> base64_decode(const std::string& text) {
  static const std::string table =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  int reverse[256];
  for (int& value : reverse) {
    value = -1;
  }
  for (std::size_t i = 0; i < table.size(); ++i) {
    reverse[static_cast<unsigned char>(table[i])] = static_cast<int>(i);
  }

  std::vector<unsigned char> out;
  out.reserve(text.size() * 3 / 4);
  int buffer = 0;
  int bits = -8;
  for (unsigned char c : text) {
    if (c == '=') {
      break;
    }
    const int value = reverse[c];
    if (value < 0) {
      continue;
    }
    buffer = (buffer << 6) | value;
    bits += 6;
    if (bits >= 0) {
      out.push_back(static_cast<unsigned char>((buffer >> bits) & 0xFF));
      bits -= 8;
    }
  }
  return out;
}

void aes_gcm_encrypt(const std::vector<unsigned char>& key,
                     const std::vector<unsigned char>& nonce,
                     const std::string& plaintext,
                     std::vector<unsigned char>& cipher,
                     std::vector<unsigned char>& tag) {
  EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
  if (ctx == nullptr) {
    throw std::runtime_error("failed to allocate cipher context");
  }
  cipher.resize(plaintext.size());
  tag.resize(16);
  int length = 0;
  int total = 0;
  bool ok = EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr,
                               nullptr) == 1 &&
            EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN,
                                static_cast<int>(nonce.size()), nullptr) == 1 &&
            EVP_EncryptInit_ex(ctx, nullptr, nullptr, key.data(),
                               nonce.data()) == 1;
  if (ok && !plaintext.empty()) {
    ok = EVP_EncryptUpdate(
             ctx, cipher.data(), &length,
             reinterpret_cast<const unsigned char*>(plaintext.data()),
             static_cast<int>(plaintext.size())) == 1;
    total = length;
  }
  if (ok) {
    ok = EVP_EncryptFinal_ex(ctx, cipher.data() + total, &length) == 1;
    total += length;
  }
  if (ok) {
    ok = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG,
                             static_cast<int>(tag.size()), tag.data()) == 1;
  }
  EVP_CIPHER_CTX_free(ctx);
  if (!ok) {
    throw std::runtime_error("failed to encrypt");
  }
  cipher.resize(total);
}

std::string aes_gcm_decrypt(const std::vector<unsigned char>& key,
                            const std::vector<unsigned char>& nonce,
                            const std::vector<unsigned char>& tag,
                            const unsigned char* cipher,
                            std::size_t cipher_len) {
  EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
  if (ctx == nullptr) {
    throw std::runtime_error("failed to allocate cipher context");
  }
  std::string plaintext(cipher_len, '\0');
  int length = 0;
  int total = 0;
  bool ok = EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr,
                               nullptr) == 1 &&
            EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN,
                                static_cast<int>(nonce.size()), nullptr) == 1 &&
            EVP_DecryptInit_ex(ctx, nullptr, nullptr, key.data(),
                               nonce.data()) == 1;
  if (ok && cipher_len > 0) {
    ok = EVP_DecryptUpdate(
             ctx, reinterpret_cast<unsigned char*>(plaintext.data()), &length,
             cipher, static_cast<int>(cipher_len)) == 1;
    total = length;
  }
  if (ok) {
    ok = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG,
                             static_cast<int>(tag.size()),
                             const_cast<unsigned char*>(tag.data())) == 1;
  }
  if (ok) {
    ok = EVP_DecryptFinal_ex(
             ctx, reinterpret_cast<unsigned char*>(plaintext.data()) + total,
             &length) == 1;
    total += length;
  }
  EVP_CIPHER_CTX_free(ctx);
  if (!ok) {
    throw std::runtime_error("wrong password or corrupted data");
  }
  plaintext.resize(total);
  return plaintext;
}

}  // namespace

std::string md5_hex(const std::string& data) {
  return to_hex(digest(EVP_md5(), data));
}

std::string aes128_ecb_encrypt_hex(const std::string& plain,
                                   const std::string& key) {
  if (key.size() != 16) {
    throw std::runtime_error("invalid AES-128 key length");
  }
  EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
  if (ctx == nullptr) {
    throw std::runtime_error("failed to allocate cipher context");
  }
  std::vector<unsigned char> out(plain.size() + 16);
  int length = 0;
  int total = 0;
  bool ok =
      EVP_EncryptInit_ex(
          ctx, EVP_aes_128_ecb(), nullptr,
          reinterpret_cast<const unsigned char*>(key.data()), nullptr) == 1;
  if (ok && !plain.empty()) {
    ok = EVP_EncryptUpdate(
             ctx, out.data(), &length,
             reinterpret_cast<const unsigned char*>(plain.data()),
             static_cast<int>(plain.size())) == 1;
    total = length;
  }
  if (ok) {
    ok = EVP_EncryptFinal_ex(ctx, out.data() + total, &length) == 1;
    total += length;
  }
  EVP_CIPHER_CTX_free(ctx);
  if (!ok) {
    throw std::runtime_error("failed to encrypt AES block");
  }
  out.resize(total);
  return to_hex(out);
}

std::string base64_encode(const unsigned char* data, std::size_t len) {
  static const char* table =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  out.reserve(((len + 2) / 3) * 4);
  std::size_t i = 0;
  while (i + 2 < len) {
    unsigned int triple = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
    out.push_back(table[(triple >> 18) & 0x3F]);
    out.push_back(table[(triple >> 12) & 0x3F]);
    out.push_back(table[(triple >> 6) & 0x3F]);
    out.push_back(table[triple & 0x3F]);
    i += 3;
  }
  if (i < len) {
    unsigned int triple = data[i] << 16;
    bool has_second = (i + 1 < len);
    if (has_second) {
      triple |= data[i + 1] << 8;
    }
    out.push_back(table[(triple >> 18) & 0x3F]);
    out.push_back(table[(triple >> 12) & 0x3F]);
    out.push_back(has_second ? table[(triple >> 6) & 0x3F] : '=');
    out.push_back('=');
  }
  return out;
}

std::string eapi_encrypt_params(const std::string& url,
                                const std::string& payload_json) {
  std::string path = url;
  const std::size_t scheme = path.find("://");
  if (scheme != std::string::npos) {
    const std::size_t slash = path.find('/', scheme + 3);
    path = slash == std::string::npos ? "/" : path.substr(slash);
  }
  const std::size_t query = path.find('?');
  if (query != std::string::npos) {
    path = path.substr(0, query);
  }
  const std::size_t eapi = path.find("/eapi/");
  if (eapi != std::string::npos) {
    path.replace(eapi, 6, "/api/");
  }

  const std::string digest_hex =
      md5_hex("nobody" + path + "use" + payload_json + "md5forencrypt");
  const std::string message =
      path + "-36cd479b6b5-" + payload_json + "-36cd479b6b5-" + digest_hex;
  return aes128_ecb_encrypt_hex(message, "e82ckenh8dichen8");
}

std::string encrypted_pic_id(const std::string& pic_id) {
  std::string xored = pic_id;
  const std::size_t magic_len = 18;
  for (std::size_t i = 0; i < xored.size(); ++i) {
    xored[i] = static_cast<char>(xored[i] ^ kPicMagic[i % magic_len]);
  }
  const std::vector<unsigned char> md5_bytes = digest(EVP_md5(), xored);
  std::string result = base64_encode(md5_bytes.data(), md5_bytes.size());
  for (char& c : result) {
    if (c == '/') {
      c = '_';
    } else if (c == '+') {
      c = '-';
    }
  }
  return result;
}

std::string pic_url(std::int64_t pic_id, int size) {
  if (pic_id <= 0) {
    return {};
  }
  const std::string id = std::to_string(pic_id);
  return "https://p3.music.126.net/" + encrypted_pic_id(id) + "/" + id +
         ".jpg?param=" + std::to_string(size) + "y" + std::to_string(size);
}

std::string url_encode(const std::string& value) {
  static const char* digits = "0123456789ABCDEF";
  std::string out;
  out.reserve(value.size() * 3);
  for (unsigned char c : value) {
    if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' ||
        c == '~') {
      out.push_back(static_cast<char>(c));
    } else {
      out.push_back('%');
      out.push_back(digits[c >> 4]);
      out.push_back(digits[c & 0x0F]);
    }
  }
  return out;
}

std::string encrypt_secret(const std::string& password,
                           const std::string& plaintext) {
  std::vector<unsigned char> salt(16);
  std::vector<unsigned char> nonce(12);
  if (RAND_bytes(salt.data(), static_cast<int>(salt.size())) != 1 ||
      RAND_bytes(nonce.data(), static_cast<int>(nonce.size())) != 1) {
    throw std::runtime_error("failed to generate random bytes");
  }

  const std::vector<unsigned char> key =
      pbkdf2_sha256(password, salt, 120000, 32);
  std::vector<unsigned char> cipher;
  std::vector<unsigned char> tag;
  aes_gcm_encrypt(key, nonce, plaintext, cipher, tag);

  std::vector<unsigned char> blob;
  blob.push_back(1);
  blob.insert(blob.end(), salt.begin(), salt.end());
  blob.insert(blob.end(), nonce.begin(), nonce.end());
  blob.insert(blob.end(), tag.begin(), tag.end());
  blob.insert(blob.end(), cipher.begin(), cipher.end());
  return base64_encode(blob.data(), blob.size());
}

std::string decrypt_secret(const std::string& password,
                           const std::string& blob) {
  const std::vector<unsigned char> data = base64_decode(blob);
  const std::size_t header = 1 + 16 + 12 + 16;
  if (data.size() <= header || data[0] != 1) {
    throw std::runtime_error("invalid encrypted data");
  }
  const unsigned char* salt = data.data() + 1;
  const unsigned char* nonce = salt + 16;
  const unsigned char* tag = nonce + 12;
  const unsigned char* cipher = tag + 16;
  const std::size_t cipher_len = data.size() - header;

  const std::vector<unsigned char> key =
      pbkdf2_sha256(password, std::vector<unsigned char>(salt, salt + 16),
                    120000, 32);
  return aes_gcm_decrypt(key, std::vector<unsigned char>(nonce, nonce + 12),
                         std::vector<unsigned char>(tag, tag + 16), cipher,
                         cipher_len);
}

}  // namespace ncm
