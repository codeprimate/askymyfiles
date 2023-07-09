#!/usr/bin/env python3

import os
import re
import hashlib
import langchain
from langchain.chains import LLMChain
from langchain.chains import SimpleSequentialChain
from langchain.document_loaders import PyPDFLoader
from langchain.embeddings import OpenAIEmbeddings
from langchain.llms import OpenAI
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.text_splitter import RecursiveCharacterTextSplitter
import chromadb
from chromadb.config import Settings

class AskMyFiles:
    def __init__(self, filename=None):
        self.filename = filename
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.recurse = False
        self.db_path = os.path.join(os.getcwd(), '.vectordatadb')
        if filename is None:
            self.working_path = os.getcwd()
        else:
            if os.path.isdir(filename):
                self.working_path = os.path.abspath(filename)
                self.recurse = True
            else:
                self.working_path = os.path.dirname(os.path.abspath(filename))

        self.relative_working_path = self.db_path + "/../"
        self.collection_name = "filedata"
        self.chromadb = None
        self.embeddings_model = OpenAIEmbeddings(openai_api_key=self.api_key)
        self.max_chars = 60000
        self.openai_model = "gpt-3.5-turbo-16k"

    def load_db(self):
        if self.chromadb is None:
            self.chromadb = chromadb.Client(Settings(chroma_db_impl="duckdb+parquet", persist_directory=self.db_path))
            self.files_collection = self.chromadb.get_or_create_collection(self.collection_name)

    def reset_db(self):
        self.load_db()
        self.chromadb.reset()

    def list_files(self):
        self.load_db()
        print(self.files_collection.get(ids=[]))

    def file_info(self,filename):
        self.load_db()
        file_hash = hashlib.sha256(filename.encode()).hexdigest()
        print(f"Finding '{filename}' ({file_hash})...")
        found_files = self.files_collection.get(where={"source": filename})
        print(found_files)


    def join_strings(self,lst):
        result = ''
        for item in lst:
            if isinstance(item, list):
                result += self.join_strings(item) + '\n'
            else:
                result += item + '\n'
        return result.strip()

    def query_db(self, string, max_chars=None):
        if max_chars == None:
            max_chars = self.max_chars
        self.load_db()
        query_embedding = self.embeddings_model.embed_query(string)
        result = self.files_collection.query(query_embeddings=[query_embedding],n_results=100,include=['documents','metadatas'])
        out = self.join_strings(result['documents'])[:max_chars]
        return out

    def get_file_list(self):
        if not self.recurse:
            relative_file_path = os.path.relpath(self.filename, self.relative_working_path)
            return [relative_file_path]

        ignore_files = []
        gitignore_path = os.path.join(self.working_path, ".gitignore")
        if os.path.exists(gitignore_path):
            print("Using .gitignore")
            with open(gitignore_path, "r") as file:
                ignore_files = file.read().splitlines()
        use_ignore = len(ignore_files) == 0
        file_list = []
        for root, dirs, files in os.walk(self.working_path):
            for file in files:
                file_path = os.path.join(root, file)
                relative_file_path = os.path.relpath(file_path, self.relative_working_path)
                if not use_ignore:
                    file_list.append(relative_file_path)
                    continue

                if not any(re.search(re.compile(ignore_file), relative_file_path) for ignore_file in ignore_files):
                    file_list.append(relative_file_path)
        return file_list

    def remove_file(self,file_name):
        self.load_db()
        found_files = self.files_collection.get(where={"source": file_name})
        if found_files == None:
            print("File not found in database.")
            return
        found_files = self.files_collection.delete(where={"source": file_name})
        print(f"Removed {file_name} from database.")
        self.chromadb.persist()
        return True


    def load_files(self):
        print("Updating AskMyFiles database...")
        self.load_db()

        chunk_size=500
        chunk_overlap=50
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for file_path in self.get_file_list():
            with open(file_path, 'r') as file:
                file_modified = os.path.getmtime(file_path)
                file_hash = hashlib.sha256(file_path.encode()).hexdigest()

                existing_record = self.files_collection.get(where={"filename_hash": file_hash})
                if ( len(existing_record['ids']) != 0 and len(existing_record['metadatas']) != 0 and existing_record['metadatas'][0]['modified'] >= file_modified ):
                    print(f"Skipped loading {file_path}")
                    continue
                else:
                    self.files_collection.delete(where={"filename_hash": file_hash})

                print(f"Creating File Embeddings for: {file_path}...",end='',flush=True)


                try:
                    if os.path.splitext(file_path)[1] == '.pdf':
                        loader = PyPDFLoader(file_path)
                        pages = loader.load_and_split()
                        content = []
                        for page in pages:
                            content.append(str(page.page_content))
                        chunks = splitter.split_text(self.join_strings(content))
                    else:
                        content = file.read()
                        chunks = splitter.split_text(content)
                except Exception as e:
                    print(f"Error reading {file_path}...skipped")
                    continue

                print(f"[{len(chunks)} chunks]",end='',flush=True)
                index = 1
                for chunk in chunks:
                    record_id = f"{file_hash}-{index}"

                    embedding_vector = self.embeddings_model.embed_query(chunk)
                    self.files_collection.upsert(
                        embeddings=embedding_vector,
                        documents=chunk,
                        metadatas={"source": file_path, "modified": file_modified, "filename_hash": file_hash},
                        ids=record_id
                    )
                    index +=1
                    print(".",end='',flush=True)
                print()

        self.chromadb.persist()

        return True

    def ask(self, query):
        llm = ChatOpenAI(temperature=0.7,model=self.openai_model)
        template = """Important Knowledge from My Library:
        BEGIN Important Knowledge
        {info}.
        END Important Knowledge

        Consider My Library when you answer my question.

        Question: {text}
        Answer:
        """
        prompt_template = PromptTemplate(input_variables=["text","info"], template=template)
        answer_chain = LLMChain(llm=llm, prompt=prompt_template)
        answer = answer_chain.run(info=self.query_db(query),text=query)
        print(answer)


import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        case = sys.argv[1]
        if case == "ask":
            query = sys.argv[2]
            service = AskMyFiles()
            service.ask(query)
            pass
        if case == "add":
            dirname = sys.argv[2]
            service = AskMyFiles(dirname)
            service.load_files()
        if case == "remove":
            dirname = sys.argv[2]
            service = AskMyFiles()
            service.remove_file(dirname)
        if case == "info":
            dirname = sys.argv[2]
            service = AskMyFiles()
            service.file_info(dirname)

    else:
        print("askymfiles ask 'question' or askmyfiles add 'path/dir'")
