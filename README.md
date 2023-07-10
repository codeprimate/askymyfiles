# askymyfiles Python App

This app creates a local database of the current directory and utilizes it in conjunction with ChatGPT for answering questions.

Data is stored in a ChromaDB database in `.vectordatadb`

### Install

You need an OpenAI API account. You figure that out and get a key.
Make sure the environment variable OPENAI_API_KEY is set.


```
# You've got python3 installed, right?
# Clone this repo somewhere convenient. git pull if you want new stuff
cd ~/Code/ && git clone https://github.com/codeprimate/askymyfiles.git
cd askmyfiles
pip install -r requirements.txt

# Make it easy to use.
ln -sf /path/to/askmyfiles.py ~/bin/askmyfiles
chmod u+x ~/bin/askmyfiles
```

## Usage

Always run askmyfiles at the root of your project folder!!!

askmyfiles looks for new and updated information.

Add a list of files or directories to ignore in `.askignore` (Like a `.gitignore`).

To add a directory or file to the local database, then "add":

```
~/bin/askymyfiles add the/path/to/file.txt
~/bin/askymyfiles add /really/the/path/to/file.txt
~/bin/askymyfiles add the/path/
~/bin/askymyfiles add /really/the/path/

# if there is a failure/interruption touch the file and retry
touch the/path/to/file.txt
~/bin/askymyfiles add the/path/to/file.txt
```

To add a webpage to the local database, then "add_webpage":

```
~/bin/askymyfiles add_webpage "https://www.example.com/example.html"
```

To ask a question using the gpt-3.5-turbo-16k model, then "ask":

```
~/bin/askmyfiles ask "Your question."
~/bin/askmyfiles "Your question"
```

To remove a file or URL from the database, then "remove":

```
# Specify a single resource to remove 
# Also useful when adding fails
~/bin/askymyfiles remove the/path/to/file.txt
~/bin/askymyfiles remove /really/the/path/to/file.txt
~/bin/askymyfiles remove "https://www.example.com/example.html"
```

Once the file is loaded into the database, you don't NEED it in the project directory anymore.

Please note that the app will only return the code snippet relevant to your question and nothing else.
API usage fees will apply.

### Back it Up

```
tar cvf my_db.tgz .vectordatadb
```

All file paths are relative. Drop your database anywhere.

# TODO

- oobabooga integration!
- Add URLs
- STDIN
