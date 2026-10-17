#include <pybind11/functional.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <functional>
#include <string>

#include "ncm/api.hpp"
#include "ncm/crypto.hpp"
#include "ncm/spectrum.hpp"
#ifndef NCM_PUBLIC
#include "ncm/musicjx.hpp"
#include "ncm/nextmusic.hpp"
#endif

namespace py = pybind11;

namespace {

std::function<bool(std::uint64_t, std::uint64_t)> make_progress(py::object progress) {
    if (progress.is_none()) {
        return {};
    }
    return [progress](std::uint64_t received, std::uint64_t total) -> bool {
        py::gil_scoped_acquire acquire;
        return progress(received, total).cast<bool>();
    };
}

}

PYBIND11_MODULE(ncm_core, m) {
    m.doc() = "Netease Cloud Music C++ core (WinHTTP + BCrypt)";

    py::class_<ncm::Client>(m, "Client")
        .def(py::init<>())
        .def("set_cookie", &ncm::Client::set_cookie, py::arg("cookie"))
        .def("cookie", &ncm::Client::cookie)
        .def("set_timeout", &ncm::Client::set_timeout, py::arg("seconds"))
        .def("resolve_id", &ncm::Client::resolve_id, py::arg("input"),
             py::call_guard<py::gil_scoped_release>())
        .def("search", &ncm::Client::search, py::arg("keywords"),
             py::arg("limit") = 30, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("song_detail", &ncm::Client::song_detail, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("song_url", &ncm::Client::song_url, py::arg("id"),
             py::arg("level"),
             py::call_guard<py::gil_scoped_release>())
        .def("song_full", &ncm::Client::song_full, py::arg("id"),
             py::arg("level"),
             py::call_guard<py::gil_scoped_release>())
        .def("lyric", &ncm::Client::lyric, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("playlist", &ncm::Client::playlist, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("album", &ncm::Client::album, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("qr_unikey", &ncm::Client::qr_unikey,
             py::call_guard<py::gil_scoped_release>())
        .def("qr_check", &ncm::Client::qr_check, py::arg("unikey"),
             py::call_guard<py::gil_scoped_release>())
        .def("login_password", &ncm::Client::login_password,
             py::arg("username"), py::arg("password"),
             py::call_guard<py::gil_scoped_release>())
        .def("user_account", &ncm::Client::user_account,
             py::call_guard<py::gil_scoped_release>())
        .def("recommend_songs", &ncm::Client::recommend_songs,
             py::call_guard<py::gil_scoped_release>())
        .def("personal_fm", &ncm::Client::personal_fm,
             py::call_guard<py::gil_scoped_release>())
        .def("toplists", &ncm::Client::toplists,
             py::call_guard<py::gil_scoped_release>())
        .def("liked_song_ids", &ncm::Client::liked_song_ids,
             py::call_guard<py::gil_scoped_release>())
        .def("like_song", &ncm::Client::like_song, py::arg("id"),
             py::arg("like"),
             py::call_guard<py::gil_scoped_release>())
        .def("user_playlists", &ncm::Client::user_playlists, py::arg("uid"),
             py::arg("limit") = 50, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("comments", &ncm::Client::comments, py::arg("id"),
             py::arg("limit") = 20, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("artist_detail", &ncm::Client::artist_detail, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("artist_songs", &ncm::Client::artist_songs, py::arg("id"),
             py::arg("limit") = 50, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("artist_albums", &ncm::Client::artist_albums, py::arg("id"),
             py::arg("limit") = 30, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("new_songs", &ncm::Client::new_songs, py::arg("area_id") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def(
            "download_song",
            [](ncm::Client& client, const std::string& id,
               const std::string& level, const std::string& directory,
               py::object progress) {
                const auto callback = make_progress(progress);
                std::string result;
                {
                    py::gil_scoped_release release;
                    result = client.download_song(id, level, directory, callback);
                }
                return result;
            },
            py::arg("id"), py::arg("level"), py::arg("directory"),
            py::arg("progress") = py::none())
        .def(
            "download_url",
            [](ncm::Client& client, const std::string& url,
               const std::string& directory, const std::string& filename,
               const std::string& referer, py::object progress, bool resume) {
                const auto callback = make_progress(progress);
                std::string result;
                {
                    py::gil_scoped_release release;
                    result = client.download_url(url, directory, filename, referer,
                                                 callback, resume);
                }
                return result;
            },
            py::arg("url"), py::arg("directory"), py::arg("filename") = "",
            py::arg("referer") = "", py::arg("progress") = py::none(),
            py::arg("resume") = false)
        .def_static("pic_url", &ncm::Client::pic_url_for, py::arg("pic_id"),
                    py::arg("size") = 300);

#ifndef NCM_PUBLIC
    py::class_<ncm::NextMusicClient>(m, "NextMusicClient")
        .def(py::init<>())
        .def("set_timeout", &ncm::NextMusicClient::set_timeout, py::arg("seconds"))
        .def("get_ip", &ncm::NextMusicClient::get_ip, py::arg("refresh") = false,
             py::call_guard<py::gil_scoped_release>())
        .def("request", &ncm::NextMusicClient::request, py::arg("path"),
             py::arg("payload_json"),
             py::call_guard<py::gil_scoped_release>())
        .def("search", &ncm::NextMusicClient::search, py::arg("keyword"),
             py::arg("limit") = 100, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("song_info", &ncm::NextMusicClient::song_info, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("song_url", &ncm::NextMusicClient::song_url, py::arg("id"),
             py::arg("level"), py::arg("immerse_type") = "",
             py::call_guard<py::gil_scoped_release>())
        .def("song_lyric", &ncm::NextMusicClient::song_lyric, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("album", &ncm::NextMusicClient::album, py::arg("id"),
             py::call_guard<py::gil_scoped_release>())
        .def("playlist_trackall", &ncm::NextMusicClient::playlist_trackall,
             py::arg("id"), py::call_guard<py::gil_scoped_release>())
        .def("artist_songs", &ncm::NextMusicClient::artist_songs, py::arg("id"),
             py::arg("limit") = 500, py::arg("offset") = 0,
             py::call_guard<py::gil_scoped_release>())
        .def("toplist", &ncm::NextMusicClient::toplist,
             py::call_guard<py::gil_scoped_release>())
        .def_static("base_url", &ncm::NextMusicClient::base_url);

    py::class_<ncm::MusicJxClient>(m, "MusicJxClient")
        .def(py::init<>())
        .def("set_timeout", &ncm::MusicJxClient::set_timeout, py::arg("seconds"))
        .def("search", &ncm::MusicJxClient::search, py::arg("input"),
             py::arg("filter") = "name", py::arg("type") = "netease",
             py::arg("page") = 1, py::call_guard<py::gil_scoped_release>())
        .def_static("base_url", &ncm::MusicJxClient::base_url);
#endif

    m.attr("__version__") = "0.3.0";

  m.def("encrypt_secret", &ncm::encrypt_secret, py::arg("password"),
        py::arg("plaintext"), py::call_guard<py::gil_scoped_release>());
  m.def("decrypt_secret", &ncm::decrypt_secret, py::arg("password"),
        py::arg("blob"), py::call_guard<py::gil_scoped_release>());

  py::class_<ncm::Spectrum>(m, "Spectrum")
      .def(py::init<>())
      .def("snapshot", &ncm::Spectrum::snapshot,
           py::call_guard<py::gil_scoped_release>())
      .def("state", &ncm::Spectrum::state);
}
