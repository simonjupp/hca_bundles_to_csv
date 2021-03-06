#!/usr/bin/env python
"""
The Flatten class will collapse a set of HCA metadata JSON documents into a single csv file"""
__author__ = "jupp"
__license__ = "Apache 2.0"
__date__ = "15/02/2019"

import sys
import re
import os
import functools
import csv
from argparse import ArgumentParser
import glob
import json

class Flatten:
    def __init__(self, order = None, ignore = None, format_filter = None):
        self.all_objects_by_project_id = {}
        self.all_keys = []
        self.uuid4hex = re.compile('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)

        self.default_order = order if order else [
             "path",
             "^\\*\\.file_core\\.file_name",
             "^\\*\\.file_core\\.file_format",
             "^sequence_file.*",
             "^analysis_file.*",
             "^donor_organism.*",
             "^specimen_from_organism.*",
             "^cell_suspension.*",
             "^.*protocol.*",
             "^project.",
             "^analysis_process.*",
             "^process.*",
             "^bundle_.*",
             "^file_.*"
        ]

        self.default_ignore = ignore if ignore else [
            "describedBy",
            "schema_type",
            "submission_date",
            "update_date",
            "biomaterial_id",
            "process_id",
            "contributors",
            "publications",
            "protocol_id",
            "project_description",
            "file_format",
            "file_name"
        ]

        self.default_format_filter = format_filter
        # TODO temp until block filetype is needed
        self.default_blocked_file_ext = {'csv', 'txt', 'pdf'}

    def _get_file_info_from_objects(self, manifest, list_of_metadata_objects):
        file_info = {}
        file_manifests = {file_manifest['name']: file_manifest
                          for file_manifest in manifest['bundle']['files'] if not file_manifest['indexed']}

        for object in list_of_metadata_objects:
            if "schema_type" not in object:
                raise MissingSchemaTypeError("JSON objects must declare a schema type")

            if object["schema_type"] == "file":
                file_name = self._deep_get(object, ["file_core", "file_name"])
                if file_name is None:
                    raise MissingFileNameError("expecting file_core.file_name")
                file_manifest = file_manifests[file_name]
                file_info[file_manifest['uuid']] = {'metadata': object, 'manifest': file_manifest}

        if not file_info:
            raise MissingFileTypeError("no fileuuids found in any of the metadata objects")

        return file_info

    def _deep_get(self, d, keys):
        if not keys or d is None:
            return d
        return self._deep_get(d.get(keys[0]), keys[1:])

    def _set_value(self, master, key, value):

        if key not in master:
            master[key] = str(value)
        else:
            existing_values = master[key].split("||")
            existing_values.append(str(value))
            uniq = sorted(list(set(existing_values)))
            master[key] = "||".join(uniq)

    def _flatten(self, master, obj, parent):
        for key, value in obj.items():
            if key in self.default_ignore :
                continue

            newkey = parent + "." + key
            if isinstance(value, dict):
                self._flatten(master, obj[key], newkey)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._flatten(master, item, newkey)
                    else:
                        self._set_value(master, newkey, item)
            else:
                self._set_value(master, newkey, value)

    def _get_schema_name_from_object(self, object):
        if "describedBy" in object:
            return object["describedBy"].rsplit('/', 1)[-1]
        raise MissingDescribedByError("found a metadata without a describedBy property")

    def _cmp_keys(self, a, b):
        '''
        Simple comparator that uses a set of ordered items with
        regular expression to roughly control the order.
        :param a:
        :param b:
        :return:
        '''
        best_a = sys.maxsize
        best_b = sys.maxsize

        for idx, val in enumerate(self.default_order):
            p = re.compile(val)

            match_a = p.match(a)
            match_b = p.match(b)

            if (match_a):
                if (idx < best_a):
                    best_a = idx

            if (match_b):
                if (idx < best_b):
                    best_b = idx

        if (best_a == best_b):
            if a >= b:
                return 1
            return -1
        return best_a - best_b

    def add_bundle_files_to_row(self, manifest, list_of_metadata_objects, dir_name=None):
        '''

        :param list_of_metadata_objects:
        :return:
        '''
        # get all the files
        file_info = self._get_file_info_from_objects(manifest, list_of_metadata_objects)

        for file_uuid, content in file_info.items():
            file_metadata = content['metadata']
            file_manifest = content['manifest']
            obj = {}

            obj['bundle_uuid'] = manifest['bundle']['uuid']
            obj['bundle_version'] = manifest['bundle']['version']
            obj['file_uuid'] = file_manifest['uuid']
            obj['file_version'] = file_manifest['version']
            obj["*.file_core.file_name"] = self._deep_get(file_metadata, ["file_core", "file_name"])
            obj["*.file_core.file_format"] = self._deep_get(file_metadata, ["file_core", "file_format"])

            file_segments = obj["*.file_core.file_name"].split('.')

            if len(file_segments) > 1 and file_segments[-1] in self.default_blocked_file_ext:
                continue

            def handle_zarray(anchor):
                file_name = obj['*.file_core.file_name']
                try:
                    i = file_name.index(anchor)
                except ValueError:
                    return False
                else:
                    i += len(anchor) - 1
                    dir_name, file_name = file_name[0:i], file_name[i+1:]
                    if file_name == '.zattrs':
                        obj['*.file_core.file_name'] = dir_name + '/'
                        return False
                return True

            if handle_zarray('.zarr/') or handle_zarray('.zarr!'):
                continue

            if dir_name:
                obj["path"] = dir_name + os.sep + obj["*.file_core.file_name"]

            if self.default_format_filter and obj["*.file_core.file_format"] not in self.default_format_filter:
                continue

            schema_name = self._get_schema_name_from_object(file_metadata)
            self._flatten(obj, file_metadata, schema_name)

            project_uuid = None
            for file_metadata in list_of_metadata_objects:

                # ignore files
                if file_metadata["schema_type"] == "file" or file_metadata["schema_type"] == "link_bundle":
                    continue
                elif file_metadata["schema_type"] == 'project':
                    project_uuid = file_metadata['provenance']['document_id']

                schema_name = self._get_schema_name_from_object(file_metadata)
                self._flatten(obj, file_metadata, schema_name)

            self.all_keys.extend(obj.keys())
            self.all_keys = list(set(self.all_keys))
            assert project_uuid is not None
            self.all_objects_by_project_id.setdefault(project_uuid, []).append(obj)

    def dump(self, filename='output.csv', delim=","):
        self.write_csv(filename, delim, [y for x in self.all_objects_by_project_id.values() for y in x])

    def dump_by_project(self, delim=','):
        file_extension = 'tsv' if delim == '\t' else 'csv'
        for project_uuid, objects in self.all_objects_by_project_id.items():
            self.write_csv(f'{project_uuid}.{file_extension}', delim, objects)

    def write_csv(self, filename, delim, objects):
        self.all_keys.sort(key=functools.cmp_to_key(self._cmp_keys))

        delim = delim

        with open(filename, 'w') as csvfile:
            csv_writer = csv.DictWriter(csvfile, self.all_keys, delimiter=delim)
            csv_writer.writeheader()
            for obj in objects:
                csv_writer.writerow(obj)


def convert_bundle_dirs():
    parser = ArgumentParser()
    parser.add_argument("-d", "--dir", dest="dirname",
                        help="path to bundle directory", metavar="FILE", default=".")
    parser.add_argument("-o", "--output", dest="filename",
                        help="path to output file", default='bundles.csv')
    parser.add_argument("-b", "--browser-manifest", dest="browser_manifest",
                        help="path to the manifest from the Data Browser", default=None)
    parser.add_argument("-s", "--seperator", dest="seperator",
                        help="seperator/delimiter for csv", default=',')
    parser.add_argument("-f", "--filter", dest="filter",
                        help="only get metadata for files with this file extension", default=None)
    parser.add_argument('--project', dest='project', help="splits csv files by project and into separate folders",
                        action='store_true')

    args = parser.parse_args()

    bundle_dir = args.dirname

    flattener = Flatten(format_filter=args.filter)

    uuid4hex = re.compile('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)

    converted_browser_manifests = {}

    if args.browser_manifest is not None:
        # convert the browser manifest to a dict of DSS manifest mapped by bundle_uuid
        with open(args.browser_manifest, 'r') as manifest_file:
            reader = csv.DictReader(manifest_file, delimiter='\t')
            for row in reader:
                bundle_uuid = row["bundle_uuid"]
                file_manifest = {
                    'name': row['file_name'],
                    'uuid': row['file_uuid'],
                    'version': row['file_version'],
                    'indexed': False
                }
                if bundle_uuid in converted_browser_manifests.keys():
                    converted_browser_manifests[bundle_uuid]['bundle']['files'].append(file_manifest)
                else:
                    converted_browser_manifests[bundle_uuid] = {
                        'bundle': {
                            'uuid': bundle_uuid,
                            'version': row["bundle_version"],
                            'files': [file_manifest]
                        }
                    }

    for bundle in os.listdir(bundle_dir):
        if '.' not in bundle:
            continue

        sep = bundle.index('.')
        bundle_uuid, bundle_version = bundle[:sep], bundle[sep+1:]

        # ignore any directory that isn't named with a uuid
        if uuid4hex.match(bundle_uuid):
            if args.browser_manifest is None:
                with open(os.path.join(bundle_dir, bundle, 'bundle.json'), 'r') as manifest_file:
                    manifest = json.load(manifest_file)
            else:
                manifest = converted_browser_manifests[bundle_uuid]
            print ("flattening " + bundle)
            metadata_files = []
            for file in glob.glob(bundle_dir + os.sep + bundle + os.sep + '*.json'):
                if 'bundle.json' not in file:
                    with open(file) as f:
                        data = json.load(f)
                        metadata_files.append(data)
            flattener.add_bundle_files_to_row(manifest, metadata_files, dir_name=bundle)

    if args.project:
        flattener.dump_by_project(delim=args.seperator)
    else:
        flattener.dump(filename=args.filename, delim=args.seperator)


if __name__ == '__main__':
    convert_bundle_dirs()

class Error(Exception):
   """Base class for other exceptions"""
   pass

class MissingSchemaTypeError(Error):
   pass

class MissingDescribedByError(Error):
   pass

class MissingFileTypeError(Error):
   pass

class MissingFileNameError(Error):
   pass