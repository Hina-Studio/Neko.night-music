#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "ncm/http.hpp"

namespace ncm {

class Client {
public:
    Client();

    void set_cookie(const std::string& cookie);
    std::string cookie() const;

    void set_timeout(int seconds);

    std::string search(const std::string& keywords, int limit = 30, int offset = 0);
    std::string song_detail(const std::string& id);
    std::string song_url(const std::string& id, const std::string& level);
    std::string song_full(const std::string& id, const std::string& level);
    std::string lyric(const std::string& id);
    std::string playlist(const std::string& id);
    std::string album(const std::string& id);

    std::string qr_unikey();
    std::string qr_check(const std::string& unikey);
    std::string login_password(const std::string& username,
                               const std::string& password);
    std::string user_account();

    std::string recommend_songs();
    std::string personal_fm();
    std::string toplists();
    std::string liked_song_ids();
    std::string like_song(const std::string& id, bool like);
    std::string user_playlists(const std::string& uid, int limit, int offset);
    std::string comments(const std::string& id, int limit, int offset);
    std::string artist_detail(const std::string& id);
    std::string artist_songs(const std::string& id, int limit, int offset);
    std::string artist_albums(const std::string& id, int limit, int offset);
    std::string new_songs(int area_id);

    std::string download_song(const std::string& id,
                              const std::string& level,
                              const std::string& directory,
                              const std::function<bool(std::uint64_t, std::uint64_t)>& progress);

    std::string download_url(const std::string& url,
                             const std::string& directory,
                             const std::string& filename,
                             const std::string& referer,
                             const std::function<bool(std::uint64_t, std::uint64_t)>& progress,
                             bool resume = false);

    std::string resolve_id(const std::string& input);

    static std::string pic_url_for(std::int64_t pic_id, int size);

private:
    Http http_;
    std::string cookie_;

    std::string post_form(const std::string& url,
                          const std::vector<std::pair<std::string, std::string>>& fields,
                          bool with_headers);
    std::string post_eapi(const std::string& url, const std::string& payload_json);
    std::string get_with_headers(const std::string& url);
    Http::Headers base_headers() const;
};

}
