/* pwfile.h — passwords in and out of files.
 *
 * Reads the CSV every other password manager exports, plus the spreadsheet a
 * lot of people actually keep their logins in. Writes the one CSV shape that
 * Chrome, Firefox and Bitwarden all read back, so an export here is not a
 * dead end.
 *
 * Neither direction touches the keyring: this module only turns files into
 * LyCredential rows and back. Storing them is passwords.c's business.
 */
#pragma once

#include "lyndon.h"
#include "passwords.h"

G_BEGIN_DECLS

/* .csv, .tsv, .txt — delimiter sniffed; .xlsx and .ods read directly, no
 * spreadsheet program involved. Returns LyCredential rows with the origin
 * already normalised, or NULL with error set.
 *
 * skipped, when asked for, counts rows that held no usable login: a login
 * needs a site and a password, and export files are full of secure notes and
 * payment cards that have neither. */
GPtrArray *ly_pwfile_read (const char *path, guint *skipped, GError **error);

/* name,url,username,password,note — the de-facto interchange format. */
gboolean ly_pwfile_write_csv (const char *path, GPtrArray *credentials,
                              GError **error);

G_END_DECLS
