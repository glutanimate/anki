# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
# pylint: skip-file

"""
Python bindings for Anki's Rust libraries.

Please do not access methods on the backend directly - they may be changed
or removed at any time. Instead, please use the methods on the collection
instead. Eg, don't use col.backend.all_deck_config(), instead use
col.decks.all_config()
"""

import enum
import json
import os
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    NewType,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import ankirspy  # pytype: disable=import-error

import anki.backend_pb2 as pb
import anki.buildinfo
from anki import hooks
from anki.dbproxy import Row as DBRow
from anki.dbproxy import ValueForDB
from anki.fluent_pb2 import FluentString as TR
from anki.sound import AVTag, SoundOrVideoTag, TTSTag
from anki.types import assert_impossible_literal
from anki.utils import intTime

assert ankirspy.buildhash() == anki.buildinfo.buildhash

SchedTimingToday = pb.SchedTimingTodayOut
BuiltinSortKind = pb.BuiltinSearchOrder.BuiltinSortKind
BackendCard = pb.Card
BackendNote = pb.Note
TagUsnTuple = pb.TagUsnTuple
NoteType = pb.NoteType
DeckTreeNode = pb.DeckTreeNode
StockNoteType = pb.StockNoteType

try:
    import orjson
except:
    # add compat layer for 32 bit builds that can't use orjson
    print("reverting to stock json")

    class orjson:  # type: ignore
        def dumps(obj: Any) -> bytes:
            return json.dumps(obj).encode("utf8")

        loads = json.loads


to_json_bytes = orjson.dumps
from_json_bytes = orjson.loads


class Interrupted(Exception):
    pass


class StringError(Exception):
    def __str__(self) -> str:
        return self.args[0]  # pylint: disable=unsubscriptable-object


NetworkErrorKind = pb.NetworkError.NetworkErrorKind
SyncErrorKind = pb.SyncError.SyncErrorKind


class NetworkError(StringError):
    def kind(self) -> NetworkErrorKind:
        return self.args[1]


class SyncError(StringError):
    def kind(self) -> SyncErrorKind:
        return self.args[1]


class IOError(StringError):
    pass


class DBError(StringError):
    pass


class TemplateError(StringError):
    pass


class NotFoundError(Exception):
    pass


class ExistsError(Exception):
    pass


class DeckIsFilteredError(Exception):
    pass


class InvalidInput(StringError):
    pass


def proto_exception_to_native(err: pb.BackendError) -> Exception:
    val = err.WhichOneof("value")
    if val == "interrupted":
        return Interrupted()
    elif val == "network_error":
        return NetworkError(err.localized, err.network_error.kind)
    elif val == "sync_error":
        return SyncError(err.localized, err.sync_error.kind)
    elif val == "io_error":
        return IOError(err.localized)
    elif val == "db_error":
        return DBError(err.localized)
    elif val == "template_parse":
        return TemplateError(err.localized)
    elif val == "invalid_input":
        return InvalidInput(err.localized)
    elif val == "json_error":
        return StringError(err.localized)
    elif val == "not_found_error":
        return NotFoundError()
    elif val == "exists":
        return ExistsError()
    elif val == "deck_is_filtered":
        return DeckIsFilteredError()
    elif val == "proto_error":
        return StringError(err.localized)
    else:
        print("unhandled error type:", val)
        return StringError(err.localized)


MediaSyncProgress = pb.MediaSyncProgress

FormatTimeSpanContext = pb.FormatTimeSpanIn.Context


class ProgressKind(enum.Enum):
    MediaSync = 0
    MediaCheck = 1


@dataclass
class Progress:
    kind: ProgressKind
    val: Union[MediaSyncProgress, str]


def proto_progress_to_native(progress: pb.Progress) -> Progress:
    kind = progress.WhichOneof("value")
    if kind == "media_sync":
        return Progress(kind=ProgressKind.MediaSync, val=progress.media_sync)
    elif kind == "media_check":
        return Progress(kind=ProgressKind.MediaCheck, val=progress.media_check)
    else:
        assert_impossible_literal(kind)


def _on_progress(progress_bytes: bytes) -> bool:
    progress = pb.Progress()
    progress.ParseFromString(progress_bytes)
    native_progress = proto_progress_to_native(progress)
    return hooks.bg_thread_progress_callback(True, native_progress)


class RustBackend:
    def __init__(
        self,
        ftl_folder: Optional[str] = None,
        langs: Optional[List[str]] = None,
        server: bool = False,
    ) -> None:
        # pick up global defaults if not provided
        if ftl_folder is None:
            ftl_folder = os.path.join(anki.lang.locale_folder, "fluent")
        if langs is None:
            langs = [anki.lang.currentLang]

        init_msg = pb.BackendInit(
            locale_folder_path=ftl_folder, preferred_langs=langs, server=server,
        )
        self._backend = ankirspy.open_backend(init_msg.SerializeToString())
        self._backend.set_progress_callback(_on_progress)

    def _run_command(
        self, input: pb.BackendInput, release_gil: bool = False
    ) -> pb.BackendOutput:
        input_bytes = input.SerializeToString()
        output_bytes = self._backend.command(input_bytes, release_gil)
        output = pb.BackendOutput()
        output.ParseFromString(output_bytes)
        kind = output.WhichOneof("value")
        if kind == "error":
            raise proto_exception_to_native(output.error)
        else:
            return output

    def open_collection(
        self, col_path: str, media_folder_path: str, media_db_path: str, log_path: str
    ):
        self._run_command(
            pb.BackendInput(
                open_collection=pb.OpenCollectionIn(
                    collection_path=col_path,
                    media_folder_path=media_folder_path,
                    media_db_path=media_db_path,
                    log_path=log_path,
                )
            ),
            release_gil=True,
        )

    def close_collection(self, downgrade=True):
        self._run_command(
            pb.BackendInput(
                close_collection=pb.CloseCollectionIn(downgrade_to_schema11=downgrade)
            ),
            release_gil=True,
        )

    def add_file_to_media_folder(self, desired_name: str, data: bytes) -> str:
        return self._run_command(
            pb.BackendInput(
                add_media_file=pb.AddMediaFileIn(desired_name=desired_name, data=data)
            )
        ).add_media_file

    def translate(self, key: TR, **kwargs: Union[str, int, float]) -> str:
        return self._run_command(
            pb.BackendInput(translate_string=translate_string_in(key, **kwargs))
        ).translate_string

    def format_time_span(
        self,
        seconds: float,
        context: FormatTimeSpanContext = FormatTimeSpanContext.INTERVALS,
    ) -> str:
        return self._run_command(
            pb.BackendInput(
                format_time_span=pb.FormatTimeSpanIn(seconds=seconds, context=context)
            )
        ).format_time_span

    def studied_today(self, cards: int, seconds: float) -> str:
        return self._run_command(
            pb.BackendInput(
                studied_today=pb.StudiedTodayIn(cards=cards, seconds=seconds)
            )
        ).studied_today

    def learning_congrats_msg(self, next_due: float, remaining: int) -> str:
        return self._run_command(
            pb.BackendInput(
                congrats_learn_msg=pb.CongratsLearnMsgIn(
                    next_due=next_due, remaining=remaining
                )
            )
        ).congrats_learn_msg

    def empty_trash(self):
        self._run_command(pb.BackendInput(empty_trash=pb.Empty()))

    def restore_trash(self):
        self._run_command(pb.BackendInput(restore_trash=pb.Empty()))

    def db_query(
        self, sql: str, args: Sequence[ValueForDB], first_row_only: bool
    ) -> List[DBRow]:
        return self._db_command(
            dict(kind="query", sql=sql, args=args, first_row_only=first_row_only)
        )

    def db_execute_many(self, sql: str, args: List[List[ValueForDB]]) -> List[DBRow]:
        return self._db_command(dict(kind="executemany", sql=sql, args=args))

    def db_begin(self) -> None:
        return self._db_command(dict(kind="begin"))

    def db_commit(self) -> None:
        return self._db_command(dict(kind="commit"))

    def db_rollback(self) -> None:
        return self._db_command(dict(kind="rollback"))

    def _db_command(self, input: Dict[str, Any]) -> Any:
        return orjson.loads(self._backend.db_command(orjson.dumps(input)))

    def abort_media_sync(self):
        self._run_command(pb.BackendInput(abort_media_sync=pb.Empty()))

    def all_tags(self) -> Iterable[TagUsnTuple]:
        return self._run_command(pb.BackendInput(all_tags=pb.Empty())).all_tags.tags

    def register_tags(self, tags: str, usn: Optional[int], clear_first: bool) -> bool:
        if usn is None:
            preserve_usn = False
            usn_ = 0
        else:
            usn_ = usn
            preserve_usn = True

        return self._run_command(
            pb.BackendInput(
                register_tags=pb.RegisterTagsIn(
                    tags=tags,
                    usn=usn_,
                    preserve_usn=preserve_usn,
                    clear_first=clear_first,
                )
            )
        ).register_tags

    def before_upload(self):
        self._run_command(pb.BackendInput(before_upload=pb.Empty()))

    def get_changed_tags(self, usn: int) -> List[str]:
        return list(
            self._run_command(
                pb.BackendInput(get_changed_tags=usn)
            ).get_changed_tags.tags
        )

    def get_config_json(self, key: str) -> Any:
        b = self._run_command(pb.BackendInput(get_config_json=key)).get_config_json
        if b == b"":
            raise KeyError
        return orjson.loads(b)

    def set_config_json(self, key: str, val: Any):
        self._run_command(
            pb.BackendInput(
                set_config_json=pb.SetConfigJson(key=key, val=orjson.dumps(val))
            )
        )

    def remove_config(self, key: str):
        self._run_command(
            pb.BackendInput(
                set_config_json=pb.SetConfigJson(key=key, remove=pb.Empty())
            )
        )

    def get_all_config(self) -> Dict[str, Any]:
        jstr = self._run_command(
            pb.BackendInput(get_all_config=pb.Empty())
        ).get_all_config
        return orjson.loads(jstr)

    def set_all_config(self, conf: Dict[str, Any]):
        self._run_command(pb.BackendInput(set_all_config=orjson.dumps(conf)))

    def get_changed_notetypes(self, usn: int) -> Dict[str, Dict[str, Any]]:
        jstr = self._run_command(
            pb.BackendInput(get_changed_notetypes=usn)
        ).get_changed_notetypes
        return orjson.loads(jstr)

    def get_stock_notetype_legacy(self, kind: StockNoteType) -> Dict[str, Any]:
        bytes = self._run_command(
            pb.BackendInput(get_stock_notetype_legacy=kind)
        ).get_stock_notetype_legacy
        return orjson.loads(bytes)

    def get_notetype_names_and_ids(self) -> List[pb.NoteTypeNameID]:
        return list(
            self._run_command(
                pb.BackendInput(get_notetype_names=pb.Empty())
            ).get_notetype_names.entries
        )

    def get_notetype_use_counts(self) -> List[pb.NoteTypeNameIDUseCount]:
        return list(
            self._run_command(
                pb.BackendInput(get_notetype_names_and_counts=pb.Empty())
            ).get_notetype_names_and_counts.entries
        )

    def get_notetype_legacy(self, ntid: int) -> Optional[Dict]:
        try:
            bytes = self._run_command(
                pb.BackendInput(get_notetype_legacy=ntid)
            ).get_notetype_legacy
        except NotFoundError:
            return None
        return orjson.loads(bytes)

    def get_notetype_id_by_name(self, name: str) -> Optional[int]:
        return (
            self._run_command(
                pb.BackendInput(get_notetype_id_by_name=name)
            ).get_notetype_id_by_name
            or None
        )

    def add_or_update_notetype(self, nt: Dict[str, Any], preserve_usn: bool) -> None:
        bjson = orjson.dumps(nt)
        id = self._run_command(
            pb.BackendInput(
                add_or_update_notetype=pb.AddOrUpdateNotetypeIn(
                    json=bjson, preserve_usn_and_mtime=preserve_usn
                )
            ),
            release_gil=True,
        ).add_or_update_notetype
        nt["id"] = id

    def remove_notetype(self, ntid: int) -> None:
        self._run_command(pb.BackendInput(remove_notetype=ntid), release_gil=True)

    def field_names_for_note_ids(self, nids: List[int]) -> Sequence[str]:
        return self._run_command(
            pb.BackendInput(field_names_for_notes=pb.FieldNamesForNotesIn(nids=nids)),
            release_gil=True,
        ).field_names_for_notes.fields

    def find_and_replace(
        self,
        nids: List[int],
        search: str,
        repl: str,
        re: bool,
        nocase: bool,
        field_name: Optional[str],
    ) -> int:
        return self._run_command(
            pb.BackendInput(
                find_and_replace=pb.FindAndReplaceIn(
                    nids=nids,
                    search=search,
                    replacement=repl,
                    regex=re,
                    match_case=not nocase,
                    field_name=field_name,
                )
            ),
            release_gil=True,
        ).find_and_replace

    def after_note_updates(
        self, nids: List[int], generate_cards: bool, mark_notes_modified: bool
    ) -> None:
        self._run_command(
            pb.BackendInput(
                after_note_updates=pb.AfterNoteUpdatesIn(
                    nids=nids,
                    generate_cards=generate_cards,
                    mark_notes_modified=mark_notes_modified,
                )
            ),
            release_gil=True,
        )

    def set_local_minutes_west(self, mins: int) -> None:
        self._run_command(pb.BackendInput(set_local_minutes_west=mins))

    def get_preferences(self) -> pb.Preferences:
        return self._run_command(
            pb.BackendInput(get_preferences=pb.Empty())
        ).get_preferences

    def set_preferences(self, prefs: pb.Preferences) -> None:
        self._run_command(pb.BackendInput(set_preferences=prefs))

    def _run_command2(self, method: int, input: Any) -> bytes:
        input_bytes = input.SerializeToString()
        try:
            return self._backend.command2(method, input_bytes)
        except Exception as e:
            err_bytes = bytes(e.args[0])
            err = pb.BackendError()
            err.ParseFromString(err_bytes)
            raise proto_exception_to_native(err)

    # The code in this section is automatically generated - any edits you make
    # will be lost.

    # @@AUTOGEN@@

    def extract_av_tags(self, text: str, question_side: bool) -> pb.ExtractAVTagsOut:
        input = pb.ExtractAVTagsIn(text=text, question_side=question_side)
        output = pb.ExtractAVTagsOut()
        output.ParseFromString(self._run_command2(1, input))
        return output

    def extract_latex(
        self, text: str, svg: bool, expand_clozes: bool
    ) -> pb.ExtractLatexOut:
        input = pb.ExtractLatexIn(text=text, svg=svg, expand_clozes=expand_clozes)
        output = pb.ExtractLatexOut()
        output.ParseFromString(self._run_command2(2, input))
        return output

    def get_empty_cards(self) -> pb.EmptyCardsReport:
        input = pb.Empty()
        output = pb.EmptyCardsReport()
        output.ParseFromString(self._run_command2(3, input))
        return output

    def render_existing_card(self, card_id: int, browser: bool) -> pb.RenderCardOut:
        input = pb.RenderExistingCardIn(card_id=card_id, browser=browser)
        output = pb.RenderCardOut()
        output.ParseFromString(self._run_command2(4, input))
        return output

    def render_uncommitted_card(
        self, note: pb.Note, card_ord: int, template: bytes, fill_empty: bool
    ) -> pb.RenderCardOut:
        input = pb.RenderUncommittedCardIn(
            note=note, card_ord=card_ord, template=template, fill_empty=fill_empty
        )
        output = pb.RenderCardOut()
        output.ParseFromString(self._run_command2(5, input))
        return output

    def strip_av_tags(self, val: str) -> str:
        input = pb.String(val=val)
        output = pb.String()
        output.ParseFromString(self._run_command2(6, input))
        return output.val

    def search_cards(self, search: str, order: pb.SortOrder) -> Sequence[int]:
        input = pb.SearchCardsIn(search=search, order=order)
        output = pb.SearchCardsOut()
        output.ParseFromString(self._run_command2(7, input))
        return output.card_ids

    def search_notes(self, search: str) -> Sequence[int]:
        input = pb.SearchNotesIn(search=search)
        output = pb.SearchNotesOut()
        output.ParseFromString(self._run_command2(8, input))
        return output.note_ids

    def local_minutes_west(self, val: int) -> int:
        input = pb.Int64(val=val)
        output = pb.Int32()
        output.ParseFromString(self._run_command2(9, input))
        return output.val

    def sched_timing_today(self) -> pb.SchedTimingTodayOut:
        input = pb.Empty()
        output = pb.SchedTimingTodayOut()
        output.ParseFromString(self._run_command2(10, input))
        return output

    def check_media(self) -> pb.CheckMediaOut:
        input = pb.Empty()
        output = pb.CheckMediaOut()
        output.ParseFromString(self._run_command2(11, input))
        return output

    def sync_media(self, hkey: str, endpoint: str) -> pb.Empty:
        input = pb.SyncMediaIn(hkey=hkey, endpoint=endpoint)
        output = pb.Empty()
        output.ParseFromString(self._run_command2(12, input))
        return output

    def trash_media_files(self, fnames: Sequence[str]) -> pb.Empty:
        input = pb.TrashMediaFilesIn(fnames=fnames)
        output = pb.Empty()
        output.ParseFromString(self._run_command2(13, input))
        return output

    def add_or_update_deck_legacy(
        self, deck: bytes, preserve_usn_and_mtime: bool
    ) -> int:
        input = pb.AddOrUpdateDeckLegacyIn(
            deck=deck, preserve_usn_and_mtime=preserve_usn_and_mtime
        )
        output = pb.DeckID()
        output.ParseFromString(self._run_command2(14, input))
        return output.did

    def deck_tree(self, include_counts: bool, top_deck_id: int) -> pb.DeckTreeNode:
        input = pb.DeckTreeIn(include_counts=include_counts, top_deck_id=top_deck_id)
        output = pb.DeckTreeNode()
        output.ParseFromString(self._run_command2(15, input))
        return output

    def deck_tree_legacy(self) -> bytes:
        input = pb.Empty()
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(16, input))
        return output.val

    def get_all_decks_legacy(self) -> bytes:
        input = pb.Empty()
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(17, input))
        return output.val

    def get_deck_id_by_name(self, val: str) -> int:
        input = pb.String(val=val)
        output = pb.DeckID()
        output.ParseFromString(self._run_command2(18, input))
        return output.did

    def get_deck_legacy(self, did: int) -> bytes:
        input = pb.DeckID(did=did)
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(19, input))
        return output.val

    def get_deck_names(
        self, skip_empty_default: bool, include_filtered: bool
    ) -> Sequence[pb.DeckNameID]:
        input = pb.GetDeckNamesIn(
            skip_empty_default=skip_empty_default, include_filtered=include_filtered
        )
        output = pb.DeckNames()
        output.ParseFromString(self._run_command2(20, input))
        return output.entries

    def new_deck_legacy(self, val: bool) -> bytes:
        input = pb.Bool(val=val)
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(21, input))
        return output.val

    def remove_deck(self, did: int) -> pb.Empty:
        input = pb.DeckID(did=did)
        output = pb.Empty()
        output.ParseFromString(self._run_command2(22, input))
        return output

    def add_or_update_deck_config_legacy(
        self, config: bytes, preserve_usn_and_mtime: bool
    ) -> int:
        input = pb.AddOrUpdateDeckConfigLegacyIn(
            config=config, preserve_usn_and_mtime=preserve_usn_and_mtime
        )
        output = pb.DeckConfigID()
        output.ParseFromString(self._run_command2(23, input))
        return output.dcid

    def all_deck_config_legacy(self) -> bytes:
        input = pb.Empty()
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(24, input))
        return output.val

    def get_deck_config_legacy(self, dcid: int) -> bytes:
        input = pb.DeckConfigID(dcid=dcid)
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(25, input))
        return output.val

    def new_deck_config_legacy(self) -> bytes:
        input = pb.Empty()
        output = pb.Bytes()
        output.ParseFromString(self._run_command2(26, input))
        return output.val

    def remove_deck_config(self, dcid: int) -> pb.Empty:
        input = pb.DeckConfigID(dcid=dcid)
        output = pb.Empty()
        output.ParseFromString(self._run_command2(27, input))
        return output

    def get_card(self, cid: int) -> pb.Card:
        input = pb.CardID(cid=cid)
        output = pb.Card()
        output.ParseFromString(self._run_command2(28, input))
        return output

    def update_card(self, input: pb.Card) -> pb.Empty:
        output = pb.Empty()
        output.ParseFromString(self._run_command2(29, input))
        return output

    def add_card(self, input: pb.Card) -> int:
        output = pb.CardID()
        output.ParseFromString(self._run_command2(30, input))
        return output.cid

    def new_note(self, ntid: int) -> pb.Note:
        input = pb.NoteTypeID(ntid=ntid)
        output = pb.Note()
        output.ParseFromString(self._run_command2(31, input))
        return output

    def add_note(self, note: pb.Note, deck_id: int) -> int:
        input = pb.AddNoteIn(note=note, deck_id=deck_id)
        output = pb.NoteID()
        output.ParseFromString(self._run_command2(32, input))
        return output.nid

    def update_note(self, input: pb.Note) -> pb.Empty:
        output = pb.Empty()
        output.ParseFromString(self._run_command2(33, input))
        return output

    def get_note(self, nid: int) -> pb.Note:
        input = pb.NoteID(nid=nid)
        output = pb.Note()
        output.ParseFromString(self._run_command2(34, input))
        return output

    def add_note_tags(self, nids: Sequence[int], tags: str) -> int:
        input = pb.AddNoteTagsIn(nids=nids, tags=tags)
        output = pb.UInt32()
        output.ParseFromString(self._run_command2(35, input))
        return output.val

    def update_note_tags(
        self, nids: Sequence[int], tags: str, replacement: str, regex: bool
    ) -> int:
        input = pb.UpdateNoteTagsIn(
            nids=nids, tags=tags, replacement=replacement, regex=regex
        )
        output = pb.UInt32()
        output.ParseFromString(self._run_command2(36, input))
        return output.val

    def cloze_numbers_in_note(self, input: pb.Note) -> Sequence[int]:
        output = pb.ClozeNumbersInNoteOut()
        output.ParseFromString(self._run_command2(37, input))
        return output.numbers

    def check_database(self) -> Sequence[str]:
        input = pb.Empty()
        output = pb.CheckDatabaseOut()
        output.ParseFromString(self._run_command2(38, input))
        return output.problems

    # @@AUTOGEN@@


def translate_string_in(
    key: TR, **kwargs: Union[str, int, float]
) -> pb.TranslateStringIn:
    args = {}
    for (k, v) in kwargs.items():
        if isinstance(v, str):
            args[k] = pb.TranslateArgValue(str=v)
        else:
            args[k] = pb.TranslateArgValue(number=v)
    return pb.TranslateStringIn(key=key, args=args)


# temporarily force logging of media handling
if "RUST_LOG" not in os.environ:
    os.environ["RUST_LOG"] = "warn,anki::media=debug,anki::dbcheck=debug"
