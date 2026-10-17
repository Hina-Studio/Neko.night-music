#include "ncm/crypto.hpp"

#include <windows.h>
#include <bcrypt.h>

#include <stdexcept>
#include <vector>

namespace ncm {
namespace {

const char* kHexDigits = "0123456789abcdef";
const char* kPicMagic = "3go8&$8*3*3h0k(2)2";

std::vector<unsigned char> digest(LPCWSTR algorithm, const std::string& data) {
    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, algorithm, nullptr, 0) < 0) {
        throw std::runtime_error("failed to open crypto algorithm provider");
    }

    DWORD object_len = 0;
    DWORD hash_len = 0;
    DWORD written = 0;
    if (BCryptGetProperty(alg, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&object_len), sizeof(DWORD), &written, 0) < 0 ||
        BCryptGetProperty(alg, BCRYPT_HASH_LENGTH,
                          reinterpret_cast<PUCHAR>(&hash_len), sizeof(DWORD), &written, 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to query crypto properties");
    }

    std::vector<unsigned char> object(object_len);
    std::vector<unsigned char> result(hash_len);

    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptCreateHash(alg, &hash, object.data(), object_len, nullptr, 0, 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to create hash");
    }

    if (!data.empty() &&
        BCryptHashData(hash, reinterpret_cast<PUCHAR>(const_cast<char*>(data.data())),
                       static_cast<ULONG>(data.size()), 0) < 0) {
        BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to hash data");
    }

    if (BCryptFinishHash(hash, result.data(), hash_len, 0) < 0) {
        BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to finish hash");
    }

    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(alg, 0);
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
    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr,
                                    BCRYPT_ALG_HANDLE_HMAC_FLAG) < 0) {
        throw std::runtime_error("failed to open PBKDF2 provider");
    }
    std::vector<unsigned char> out(static_cast<std::size_t>(length));
    if (BCryptDeriveKeyPBKDF2(
            alg,
            reinterpret_cast<PUCHAR>(const_cast<char*>(password.data())),
            static_cast<ULONG>(password.size()),
            reinterpret_cast<PUCHAR>(const_cast<unsigned char*>(salt.data())),
            static_cast<ULONG>(salt.size()), iterations, out.data(),
            static_cast<ULONG>(out.size()), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to derive key");
    }
    BCryptCloseAlgorithmProvider(alg, 0);
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

}  // namespace

std::string md5_hex(const std::string& data) {
    return to_hex(digest(BCRYPT_MD5_ALGORITHM, data));
}

std::string aes128_ecb_encrypt_hex(const std::string& plain, const std::string& key) {
    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_AES_ALGORITHM, nullptr, 0) < 0) {
        throw std::runtime_error("failed to open AES provider");
    }
    if (BCryptSetProperty(alg, BCRYPT_CHAINING_MODE,
                          reinterpret_cast<PUCHAR>(
                              const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_ECB)),
                          sizeof(BCRYPT_CHAIN_MODE_ECB), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to set ECB mode");
    }

    BCRYPT_KEY_HANDLE key_handle = nullptr;
    if (BCryptGenerateSymmetricKey(alg, &key_handle, nullptr, 0,
                                   reinterpret_cast<PUCHAR>(const_cast<char*>(key.data())),
                                   static_cast<ULONG>(key.size()), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to import AES key");
    }

    const std::size_t block_size = 16;
    const std::size_t pad = block_size - (plain.size() % block_size);
    std::string padded = plain;
    padded.append(pad, static_cast<char>(pad));

    DWORD out_len = 0;
    if (BCryptEncrypt(key_handle,
                      reinterpret_cast<PUCHAR>(const_cast<char*>(padded.data())),
                      static_cast<ULONG>(padded.size()), nullptr, nullptr, 0,
                      nullptr, 0, &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to size AES output");
    }

    std::vector<unsigned char> out(out_len);
    if (BCryptEncrypt(key_handle,
                      reinterpret_cast<PUCHAR>(const_cast<char*>(padded.data())),
                      static_cast<ULONG>(padded.size()), nullptr, nullptr, 0,
                      out.data(), out_len, &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to encrypt AES block");
    }

    BCryptDestroyKey(key_handle);
    BCryptCloseAlgorithmProvider(alg, 0);
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

std::string eapi_encrypt_params(const std::string& url, const std::string& payload_json) {
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
    const std::vector<unsigned char> md5_bytes = digest(BCRYPT_MD5_ALGORITHM, xored);
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
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
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
    if (BCryptGenRandom(nullptr, salt.data(), static_cast<ULONG>(salt.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
        BCryptGenRandom(nullptr, nonce.data(), static_cast<ULONG>(nonce.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
        throw std::runtime_error("failed to generate random bytes");
    }

    const std::vector<unsigned char> key = pbkdf2_sha256(password, salt, 120000, 32);

    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_AES_ALGORITHM, nullptr, 0) < 0) {
        throw std::runtime_error("failed to open AES provider");
    }
    if (BCryptSetProperty(alg, BCRYPT_CHAINING_MODE,
                          reinterpret_cast<PUCHAR>(
                              const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_GCM)),
                          sizeof(BCRYPT_CHAIN_MODE_GCM), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to set GCM mode");
    }
    BCRYPT_KEY_HANDLE key_handle = nullptr;
    if (BCryptGenerateSymmetricKey(alg, &key_handle, nullptr, 0,
                                   reinterpret_cast<PUCHAR>(
                                       const_cast<unsigned char*>(key.data())),
                                   static_cast<ULONG>(key.size()), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to import key");
    }

    std::vector<unsigned char> tag(16);
    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO info;
    BCRYPT_INIT_AUTH_MODE_INFO(info);
    info.pbNonce = nonce.data();
    info.cbNonce = static_cast<ULONG>(nonce.size());
    info.pbTag = tag.data();
    info.cbTag = static_cast<ULONG>(tag.size());

    DWORD out_len = 0;
    if (BCryptEncrypt(key_handle,
                      reinterpret_cast<PUCHAR>(
                          const_cast<char*>(plaintext.data())),
                      static_cast<ULONG>(plaintext.size()), &info, nullptr, 0,
                      nullptr, 0, &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to size encrypted output");
    }
    std::vector<unsigned char> cipher(out_len);
    if (BCryptEncrypt(key_handle,
                      reinterpret_cast<PUCHAR>(
                          const_cast<char*>(plaintext.data())),
                      static_cast<ULONG>(plaintext.size()), &info, nullptr, 0,
                      cipher.data(), out_len, &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to encrypt");
    }
    BCryptDestroyKey(key_handle);
    BCryptCloseAlgorithmProvider(alg, 0);

    std::vector<unsigned char> blob;
    blob.push_back(1);
    blob.insert(blob.end(), salt.begin(), salt.end());
    blob.insert(blob.end(), nonce.begin(), nonce.end());
    blob.insert(blob.end(), tag.begin(), tag.end());
    blob.insert(blob.end(), cipher.begin(), cipher.end());
    return base64_encode(blob.data(), blob.size());
}

std::string decrypt_secret(const std::string& password, const std::string& blob) {
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

    BCRYPT_ALG_HANDLE alg = nullptr;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_AES_ALGORITHM, nullptr, 0) < 0) {
        throw std::runtime_error("failed to open AES provider");
    }
    if (BCryptSetProperty(alg, BCRYPT_CHAINING_MODE,
                          reinterpret_cast<PUCHAR>(
                              const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_GCM)),
                          sizeof(BCRYPT_CHAIN_MODE_GCM), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to set GCM mode");
    }
    BCRYPT_KEY_HANDLE key_handle = nullptr;
    if (BCryptGenerateSymmetricKey(alg, &key_handle, nullptr, 0,
                                   reinterpret_cast<PUCHAR>(
                                       const_cast<unsigned char*>(key.data())),
                                   static_cast<ULONG>(key.size()), 0) < 0) {
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("failed to import key");
    }

    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO info;
    BCRYPT_INIT_AUTH_MODE_INFO(info);
    info.pbNonce = const_cast<PUCHAR>(nonce);
    info.cbNonce = 12;
    info.pbTag = const_cast<PUCHAR>(tag);
    info.cbTag = 16;

    DWORD out_len = 0;
    if (BCryptDecrypt(key_handle, const_cast<PUCHAR>(cipher),
                      static_cast<ULONG>(cipher_len), &info, nullptr, 0,
                      nullptr, 0, &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("wrong password or corrupted data");
    }
    std::string plaintext(out_len, '\0');
    if (BCryptDecrypt(key_handle, const_cast<PUCHAR>(cipher),
                      static_cast<ULONG>(cipher_len), &info, nullptr, 0,
                      reinterpret_cast<PUCHAR>(plaintext.data()), out_len,
                      &out_len, 0) < 0) {
        BCryptDestroyKey(key_handle);
        BCryptCloseAlgorithmProvider(alg, 0);
        throw std::runtime_error("wrong password or corrupted data");
    }
    plaintext.resize(out_len);
    BCryptDestroyKey(key_handle);
    BCryptCloseAlgorithmProvider(alg, 0);
    return plaintext;
}

}
