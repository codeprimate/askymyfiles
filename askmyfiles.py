#!/usr/bin/env python3

import os
import re
import sys
import time
import concurrent.futures
import hashlib
from itertools import islice
import chromadb
from chromadb.config import Settings
from langchain.chains import LLMChain
from langchain.chains import SimpleSequentialChain
from langchain.chat_models import ChatOpenAI
from langchain.document_loaders import PyPDFLoader
from langchain.embeddings import OpenAIEmbeddings
from langchain.llms import OpenAI
from langchain.prompts import PromptTemplate
from langchain.text_splitter import RecursiveCharacterTextSplitter

class AskMyFiles:
    def __init__(self, filename=None):
        self.filename = filename
        self.db_path = os.path.join(os.getcwd(), '.vectordatadb')
        self.relative_working_path = self.db_path + "/../"
        if filename is None:
            self.working_path = os.getcwd()
        else:
            if os.path.isdir(filename):
                self.working_path = os.path.abspath(filename)
                self.recurse = True
            else:
                self.working_path = os.path.dirname(os.path.abspath(filename))
                self.recurse = False

        self.collection_name = "filedata"
        self.chromadb = None
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.embeddings_model = OpenAIEmbeddings(openai_api_key=self.api_key)

        self.max_tokens = 14000
        self.max_chars = 60000
        self.openai_model = "gpt-3.5-turbo-16k"
        self.chunk_size = 500
        self.chunk_overlap = 50

    def load_db(self):
        if self.chromadb is None:
            self.chromadb = chromadb.Client(Settings(chroma_db_impl="duckdb+parquet", persist_directory=self.db_path))
            self.files_collection = self.chromadb.get_or_create_collection(self.collection_name)
        if self.files_collection is None:
            self.files_collection = self.chromadb.get_or_create_collection(self.collection_name)

    def persist_db(self):
        self.chromadb.persist()

    def reset_db(self):
        self.load_db()
        self.chromadb.reset()

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
        self.persist_db()
        return True

    def vectorize_text(self, text):
        return self.embeddings_model.embed_query(text)

    def vectorize_chunk(self, chunk, metadata, index):
        embedding = self.vectorize_text(chunk)
        cid = f"{metadata['file_hash']}-{index}"
        return {"id": cid, "document": chunk, "embedding": embedding, "metadata": metadata}

    def vectorize_chunks(self, chunks, metadata):
        max_threads = min(len(chunks), 5)
        vectorized_chunks = {}
        # for index in range(1,len(chunks)):
        #     vectorized_chunks[str(index)] = self.vectorize_chunk(chunks[index-1], metadata, index)

        cindex = 1
        iterator = iter(chunks)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
            for chunk_group in zip(*[iterator] * max_threads):
                starting_index = cindex
                num_threads = min(max_threads, len(chunk_group))
                futures = []
                for thread_index in range(num_threads):
                    futures.append(executor.submit(self.vectorize_chunk, chunk_group[thread_index], metadata, cindex))
                    cindex += 1
                i = 0
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    chunk_index = starting_index + i
                    vectorized_chunks[f"chunk-{chunk_index}"] = result
                    print(".",end="",flush=True)
                    i += 1
                concurrent.futures.wait(futures)

        return vectorized_chunks

    def read_file(self, file_path):
        with open(file_path, 'r') as file:
            try:
                if os.path.splitext(file_path)[1] == '.pdf':
                    # PDF Processing
                    loader = PyPDFLoader(file_path)
                    pages = loader.load_and_split()
                    content = []
                    for page in pages:
                        content.append(str(page.page_content))
                    return self.join_strings(content)
                else:
                    # Plain Text Processing
                    return file.read()
            except Exception as e:
                print(f"Error reading {file_path}...skipped")
                return None

    def process_file(self,file_path):
        self.load_db()
        start_time = time.time()

        # Get file meta information
        metadata = {
            "source": file_path,
            "file_path": file_path,
            "file_modified": os.path.getmtime(file_path),
            "file_hash": hashlib.sha256(file_path.encode()).hexdigest()
        }

        # File exists?
        existing_record = self.files_collection.get(where={"file_hash": metadata["file_hash"]})
        existing = len(existing_record['ids']) != 0 and len(existing_record['metadatas']) != 0
        if existing:
            file_updated = existing_record['metadatas'][0]["file_modified"] < metadata["file_modified"]
        else:
            file_updated = True

        # Skip File?
        skip_file = existing and not file_updated
        if skip_file:
            print(f"Skipped loading {file_path}")
            return False

        print(f"Creating File Embeddings for: {file_path}...",end='',flush=True)

        splitter = RecursiveCharacterTextSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        content = self.read_file(file_path)
        chunks = splitter.split_text(content)
        chunk_count = len(chunks)
        print(f"[{len(chunks)} chunks]",end='',flush=True)

        vectorized_chunks = self.vectorize_chunks(chunks, metadata)
        chunk_keys = list(vectorized_chunks.keys())
        if len(chunk_keys) == 0:
            print("Processing Error...NO CHUNKS???")
            return False

        self.files_collection.delete(where={"file_hash": metadata["file_hash"]})
        group_size = 10
        batches = [chunk_keys[i:i+group_size] for i in range(0, len(chunk_keys), group_size)]
        for batch in batches:
            self.files_collection.add(
                ids=[vectorized_chunks[cid]['id'] for cid in batch],
                embeddings=[vectorized_chunks[cid]['embedding'] for cid in batch],
                documents=[vectorized_chunks[cid]['document'] for cid in batch],
                metadatas=[vectorized_chunks[cid]['metadata'] for cid in batch]
            )
            print("+", end='', flush=True)

        elapsed_time = max(1, int( time.time() - start_time ))
        print(f"OK [{elapsed_time}s]", flush=True)

        return True

    def load_files(self):
        print("Updating AskMyFiles database...")
        saved_files = False
        for file_path in self.get_file_list():
            saved_files = saved_files or self.process_file(file_path)

        if saved_files:
            self.persist_db()

        return saved_files

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
