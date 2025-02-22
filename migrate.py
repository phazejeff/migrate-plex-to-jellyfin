from typing import List, Set

import requests
import urllib3
import click
import sys
from loguru import logger

from plexapi.server import PlexServer
from plexapi import library
from plexapi.media import Media
from jellyfin_client import JellyFinServer


LOG_FORMAT = ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
              "<level>{level: <8}</level> | "
              "<level>{message}</level> | "
              "{extra}")

@click.command()
@click.option('--debug', required=False, help='Log at debug level')
@click.option('--plex-url', required=True, help='Plex server url')
@click.option('--plex-token', required=True, help='Plex token')
@click.option('--jellyfin-url', help='Jellyfin server url')
@click.option('--jellyfin-token', help='Jellyfin token')
@click.option('--jellyfin-user', help='Jellyfin user')
@click.option('--secure/--insecure', help='Verify SSL')
@click.option('--debug/--no-debug', help='Print more output')
@click.option('--no-skip/--skip', help='Skip when no match it found instead of exiting')
def migrate(plex_url: str, plex_token: str, jellyfin_url: str,
            jellyfin_token: str, jellyfin_user: str,
            secure: bool, debug: bool, no_skip: bool):
    logger.remove()
    if debug:
        logger.add(sys.stderr, format=LOG_FORMAT, level="DEBUG")
    else:
        logger.add(sys.stderr, format=LOG_FORMAT, level="INFO")

    # Remove insecure request warnings
    if not secure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Setup sessions
    session = requests.Session()
    session.verify = secure
    plex = PlexServer(plex_url, plex_token, session=session)

    jellyfin = JellyFinServer(
        url=jellyfin_url, api_key=jellyfin_token, session=session)

    # Watched list from Plex
    plex_watched = set()

    # All the items in jellyfish:
    jf_uid = jellyfin.get_user_id(name=jellyfin_user)
    jf_library = jellyfin.get_all(user_id=jf_uid)
    jf_entries: dict[str, List[dict]] = {} # map of path -> jf library entry
    for jf_entry in jf_library:
        for source in jf_entry.get("MediaSources", []):
            if "Path" not in source:
                continue
            if source["Path"] not in jf_entries:
                jf_entries[source["Path"]] = [jf_entry]
            else:
                jf_entries[source["Path"]].append(jf_entry)
            logger.bind(path=source["Path"], id=jf_entry["Id"]).debug("jf entry")

    # Get all Plex watched movies
    for section in plex.library.sections():
        if isinstance(section, library.MovieSection):
            plex_movies = section
            for m in plex_movies.search(unwatched=False):
                parts=_watch_parts(m.media)
                plex_watched.update(parts)
                logger.bind(section=section.title, movie=m, parts=parts).debug("watched movie")
        elif isinstance(section, library.ShowSection):
            plex_tvshows = section
            for show in plex_tvshows.searchShows(**{"episode.unwatched": False}):
                for e in show.watched():
                    parts=_watch_parts(e.media)
                    plex_watched.update(parts)
                    logger.bind(section=section.title, ep=e, parts=parts).debug("watched episode")


    marked = 0
    missing = 0
    skipped = 0
    for watched in plex_watched:
        if watched not in jf_entries:
            logger.bind(path=watched).warning("no match found on jellyfin")
            missing += 1
            continue
        for jf_entry in jf_entries[watched]:
            if not jf_entry["UserData"]["Played"]:
                marked += 1
                jellyfin.mark_watched(user_id=jf_uid, item_id=jf_entry["Id"])
                logger.bind(path=watched, jf_id=jf_entry["Id"], title=jf_entry["Name"]).info("Marked as watched")
            else:
                skipped += 1
                logger.bind(path=watched, jf_id=jf_entry["Id"], title=jf_entry["Name"]).debug("Skipped marking already-watched media")

    logger.bind(updated=marked, missing=missing, skipped=skipped).success(f"Succesfully migrated watched states to jellyfin")


def _watch_parts(media: List[Media]) -> Set[str]:
    watched = set()
    for medium in media:
        watched.update(map(lambda p: p.file, medium.parts))
    return watched

if __name__ == '__main__':
    migrate()

import requests
