''' Export Confluence pages for given ID while preserving the hierarchy '''
from pathlib import Path
import sys
import json
import logging
import requests
import html2text

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(levelname)s | %(message)s')


class Confluence:
    ''' Confluence API wrapper '''

    def __init__(self, url, username, password) -> None:
        self.url = url.rstrip('/')
        self.username = username
        self.password = password

        self.session = requests.Session()
        self.session.auth = (username, password)

        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.body_width = 0

    def get_page_by_id(self, page_id: str) -> dict:
        ''' Get page by ID '''
        api = f'{self.url}/wiki/api/v2/pages/{page_id}'
        result = self.session.get(api)
        return result.json()

    def get_page_content(self, page_id: str, version: int | None = None) -> dict:
        ''' Get page content with body and metadata '''
        api = f'{self.url}/wiki/rest/api/content/{page_id}'
        params = {"expand": "body.storage,version,space"}
        if version is not None:
            params["status"] = "historical"
            params["version"] = version
        result = self.session.get(api, params=params)
        return result.json()

    def get_page_ancestors(self, page_id: str) -> list:
        ''' Returns all ancestors for a given page by ID '''
        api = f'{self.url}/wiki/api/v2/pages/{page_id}/ancestors'
        result = self.session.get(api)
        return result.json()['results']

    def get_page_children(self, page_id: str) -> list:
        ''' Returns all child pages for given page ID '''
        api = f'{self.url}/wiki/api/v2/pages/{page_id}/children'
        result = self.session.get(api)
        return result.json()['results']

    def get_all_child_pages(self, page_id: str) -> list:
        ''' Returns all child pages for given page ID '''
        children = self.get_page_children(page_id)
        pages = []

        for child in children:
            sys.stdout.write('Add ID ' + child['id'])
            sys.stdout.flush()
            sys.stdout.write('\r')

            pages.append(child)
            pages.extend(self.get_all_child_pages(child['id']))

        return pages

    def get_page_versions(self, page_id: str) -> list:
        ''' Get all versions of a page '''
        api = f'{self.url}/wiki/rest/api/content/{page_id}/version'
        result = self.session.get(api)
        if result.status_code == 200:
            return result.json().get('results', [])
        logging.warning('Failed to get versions for page %s', page_id)
        return []

    def secure_string(self, string: str) -> str:
        ''' Remove characters that might affect the filename '''
        result = ''.join(char for char in string if (
            char.isalnum() or char in '._- '))
        return result

    def page_to_doc(self, page_id: str, dir_path: Path | str) -> None:
        ''' Save page as doc '''
        page_title = self.get_page_by_id(page_id)['title']
        file_name = self.secure_string(f'{page_title}_{page_id}.doc')
        export_api = f'{self.url}/wiki/exportword?pageId={page_id}'

        content = self.session.get(export_api).content
        Path(dir_path).mkdir(exist_ok=True, parents=True)

        try:
            with open(dir_path/file_name, 'wb',) as file:
                file.write(content)
            logging.info('Page %s saved as doc', page_id)
        except requests.exceptions.ReadTimeout:
            logging.warning('Timeout error: page ID - %s', page_id)
        except requests.exceptions.HTTPError:
            logging.warning('HTTPError error: page ID - %s', page_id)
        except OSError:
            logging.warning('Filename error: page ID - %s', page_id)

    def page_to_markdown(self, page_id: str, dir_path: Path | str,
                         version: int | None = None) -> None:
        ''' Save page as Markdown '''
        page_data = self.get_page_content(page_id, version=version)

        if 'statusCode' in page_data:
            logging.warning('Failed to get page %s (version %s): %s',
                            page_id, version, page_data.get('message', ''))
            return

        page_title = page_data['title']
        html_content = page_data['body']['storage']['value']
        page_version = page_data['version']['number']
        version_date = page_data['version']['when'][:10]
        space_key = page_data['space']['key']

        markdown_content = self.converter.handle(html_content)

        version_info = f' (version {version})' if version else ''
        header = f'# {page_title}{version_info}\n\n'
        header += f'**Space:** {space_key}\n'
        header += f'**Page ID:** {page_id}\n'
        header += f'**Version:** {page_version}\n'
        header += f'**Date:** {version_date}\n'
        header += f'**URL:** {self.url}/wiki/spaces/{space_key}/pages/{page_id}\n\n'
        header += '---\n\n'

        full_content = header + markdown_content

        if version is not None:
            file_name = self.secure_string(
                f'{page_title}_v{version}_{version_date}.md')
        else:
            file_name = self.secure_string(f'{page_title}_{page_id}.md')

        Path(dir_path).mkdir(exist_ok=True, parents=True)

        try:
            with open(dir_path/file_name, 'w', encoding='utf-8') as file:
                file.write(full_content)
            logging.info('Page %s (version %s) saved as Markdown',
                         page_id, version or 'latest')
        except OSError:
            logging.warning('Filename error: page ID - %s', page_id)

    def export_page(self, page_id: str, dir_path: Path | str,
                    fmt: str = 'doc', export_versions: bool = False) -> None:
        ''' Export a single page in the given format, optionally with version history '''
        if fmt == 'markdown':
            self.page_to_markdown(page_id, dir_path)
            if export_versions:
                versions = self.get_page_versions(page_id)
                if versions:
                    versions_dir = dir_path / 'versions'
                    for ver in versions:
                        ver_num = ver['number']
                        ver_date = ver['when'][:10]
                        ver_dir = versions_dir / f'v{ver_num}_{ver_date}'
                        self.page_to_markdown(page_id, ver_dir, version=ver_num)
        else:
            self.page_to_doc(page_id, dir_path)

    def build_page_path(self, page_id: str, root_page_id: str,
                        output_dir: Path) -> Path:
        ''' Build the full directory path for a page based on its ancestors '''
        ancestor_ids = []
        ancestor_titles = []
        page_full_path = []

        for ancestor in self.get_page_ancestors(page_id):
            ancestor_ids.append(ancestor['id'])
            title = self.get_page_by_id(ancestor['id'])['title']
            ancestor_titles.append(self.secure_string(title))

        root_index = ancestor_ids.index(root_page_id)
        for index in range(root_index, len(ancestor_ids)):
            page_full_path.append(f'{ancestor_titles[index]}_{ancestor_ids[index]}')

        return output_dir / '/'.join(page_full_path)


def main():
    ''' Entry point '''
    try:
        with open(Path(__file__).parent/'config.json', 'r', encoding='utf-8') as file:
            config = json.load(file)

        required_keys = {'url', 'email', 'token', 'pageId'}
        if not required_keys.issubset(config.keys()):
            raise KeyError(f'Missing required keys: {required_keys - config.keys()}')
    except FileNotFoundError:
        sys.exit('config.json not found')
    except (json.decoder.JSONDecodeError, KeyError, AttributeError) as e:
        sys.exit(f'Invalid config.json: {e}')

    output_dir = Path(__file__).parent / 'output'
    export_format = config.get('format', 'doc')
    export_versions = config.get('export_versions', False)
    page_ids = config.get('pageIds', [config['pageId']])

    confluence = Confluence(
        url=config['url'],
        username=config['email'],
        password=config['token'])

    for root_page_id in page_ids:
        logging.info('Processing root page %s', root_page_id)

        pages = confluence.get_all_child_pages(root_page_id)

        confluence.export_page(root_page_id, output_dir,
                               fmt=export_format,
                               export_versions=export_versions)

        for page in pages:
            dir_path = confluence.build_page_path(
                page['id'], root_page_id, output_dir)
            confluence.export_page(page['id'], dir_path,
                                   fmt=export_format,
                                   export_versions=export_versions)

    logging.info('Export complete')


if __name__ == '__main__':
    main()
