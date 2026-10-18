
<p align="center">
  <img src="assets/moviq-icon.svg" width="96" alt="Moviq icon">
</p>



# Moviq | Media Renamer

Moviq turns messy movie and TV show into a clean, organized media library. It uses [TMDB](https://www.themoviedb.org/) to identify your files, rename movies and episodes with accurate titles and metadata, and arrange them into a folder structure compatible with media servers like Plex and Jellyfin.

Preview your new filenames and folder structure, review matches, and approve changes before they're applied. Your media library, organized your way.

<br>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/moviq-banner-dark.svg">
    <img src="assets/moviq-banner.svg" width="100%" alt="Moviq banner">
  </picture>
</p>

<br>

## Features

- **Movie and TV matching**: Find movies, TV shows, and episodes using TMDB.
- **Automatic and manual matching**: Match files automatically or search for the correct title yourself.
- **Preview before renaming**: Review filenames and destination paths before making changes.
- **TV episode detection**: Recognize common season and episode patterns, including abbreviated filenames and context from parent folders.
- **Custom naming and folders**: Configure filename formats and folder structures for movies and TV episodes.
- **File safety**: Avoid overwriting existing files, review uncertain matches, and undo completed renames.
- **Saved preferences**: Keep your settings, theme, last-used folder, and window position between sessions.
- **TMDB caching**: Reuse cached responses to speed up repeat lookups.
- **Update checker**: Check for new releases from the About page.

## Demo

<img width="800" height="450" alt="ezgif-1b0ab9f8f00a7f7c" src="https://github.com/user-attachments/assets/9542ec21-ee61-4715-a16e-d260f3ae6222" />



<!-- https://github.com/user-attachments/assets/3df59574-6288-4681-a905-6f62033c0362 -->



## Download for Windows

Download either the versioned portable ZIP archive or Windows installer from the project's [GitHub Releases](https://github.com/asadse7en/Moviq/releases) page.


## Getting Started

1. Download and launch Moviq.
2. Open Settings and enter your TMDB API key.
3. Select the movie or TV show files or folders you want to organize.
4. Review the suggested matches and proposed filenames.
5. Apply the changes when you're satisfied with the results.

You can get API key from [TMDB](https://www.themoviedb.org/).



## Run From Source

To run Moviq from the source code, install Python 3.9 or newer and clone the repository.


```bash
git clone https://github.com/asadse7en/Moviq.git
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Install the application and its optional GUI dependencies:

```bash
python -m pip install -e ".[gui]"
```

Run Moviq:

```bash
python -m moviq
```

Enter your TMDB API key in Settings before using the matching features. Get a free API key from [TMDB](https://www.themoviedb.org/settings/api).

## Running Tests

To run the test suite from the project directory:

```bash
python -m unittest discover -s tests -v
```


## Feedback and Issues

Found a bug or have an idea for an improvement? [Open an issue on GitHub](https://github.com/asadse7en/Moviq/issues).
