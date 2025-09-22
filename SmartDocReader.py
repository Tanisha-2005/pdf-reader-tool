import os
import json
import io
import fitz
import pkg_resources
from tkinter import Tk, Frame, Canvas, Text, Scrollbar, Button, Entry, Label, Listbox, X, Toplevel, simpledialog, messagebox, BOTH, END, LEFT, RIGHT, Y, W, WORD
from tkinter import filedialog
from PIL import Image, ImageTk, ImageDraw
from tkinter import SINGLE, DISABLED

from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lex_rank import LexRankSummarizer

from transformers import pipeline
from docx import Document
from pptx import Presentation
from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup
import html2text
from PyPDF2 import PdfReader, PdfWriter
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import mammoth
from tkhtmlview import HTMLLabel
import logging

# Configure logging for detailed debugging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

def protect_pdf(input_path, output_path, password):
    """Encrypt a PDF file using PyPDF2 with AES-128 and common permissions."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()

        for page in reader.pages:
            writer.add_page(page)

        # Use AES-128 encryption with permissions for printing, copying, and annotating
        writer.encrypt(
            user_password=password,
            owner_password=password,
            permissions_flag=0b111100110100,  # Allow print, copy, annotate
            use_128bit=True
        )

        with open(output_path, "wb") as f:
            writer.write(f)
        logging.debug(f"Encrypted PDF created: {output_path} with password (length: {len(password)})")
        return True
    except Exception as e:
        logging.error(f"Failed to encrypt PDF {input_path}: {str(e)}")
        raise ValueError(f"Failed to encrypt PDF: {str(e)}")

class SmartDocReader:
    def __init__(self, root):
        self.root = root
        self.current_page = 0   
        self.bookmarks = []     
        self.total_pages = 10  # Example, you need to set this when opening a file

        self.root = root
        self.root.title("📄 Smart Document Reader with Summarizer")
        self.root.geometry("1200x850")
        self.root.configure(bg="#1e1e1e")

        self.page_widgets = []
        self.tk_images = []
        self.drawing = False
        self.canvas = None
        self.inner_frame = None
        self.canvas_window = None
        self.file_path = ""
        self.text_box = None
        self.text_entry = None
        self.doc_text = ""
        self.current_text_widget = None
        self.protected_files = self.load_protected_files()
        self.page_count_label = None
        self.pdf_doc = None  # Initialize class-level PDF document storage
        self.eraser_mode = False
        self.draw_highlights = {}
        self.search_highlights = {}

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Close pdf_doc and destroy window."""
        if self.pdf_doc:
            self.pdf_doc.close()
            self.pdf_doc = None
        self.root.destroy()

    def load_protected_files(self):
        """Load protected files from a JSON file."""
        try:
            with open("protected_files.json", "r") as f:
                data = json.load(f)
                logging.debug(f"Loaded protected files: {list(data.keys())}")
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            logging.debug("No protected_files.json found or invalid, returning empty dict")
            return {}

    def save_protected_files(self):
        """Save protected files to a JSON file."""
        try:
            with open("protected_files.json", "w") as f:
                json.dump(self.protected_files, f, indent=4)
            logging.debug("Saved protected_files.json")
        except Exception as e:
            logging.error(f"Failed to save protected files: {str(e)}")
            messagebox.showerror("Error", f"Failed to save protected files: {str(e)}")

    def hash_password(self, password):
        """Hash a password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'password_salt_',
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))
        return key.decode('utf-8')

    def prompt_password(self, file_path):
        """Prompt for password and handle PDF/non-PDF cases."""
        file_path = os.path.normpath(file_path)
        stored_password = self.protected_files.get(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf" and stored_password:
            logging.debug(f"Using stored password for PDF {file_path} (length: {len(stored_password)})")
            if self.test_pdf_encryption_fitz(file_path, stored_password):
                logging.debug(f"Password verified with PyMuPDF for {file_path}")
                return stored_password
            logging.warning(f"Stored password failed for {file_path}, prompting user")

        password = simpledialog.askstring("Password", f"Enter password for {os.path.basename(file_path)}:", show="*")
        if password is None:
            logging.debug(f"Password prompt cancelled for {file_path}")
            return None
        password = password.strip()
        logging.debug(f"Entered password for {file_path} (length: {len(password)})")

        if ext == ".pdf":
            if self.test_pdf_encryption_fitz(file_path, password):
                logging.debug(f"Password verified with PyMuPDF for {file_path}")
                return password
            logging.error(f"Password verification failed with PyMuPDF for {file_path}")
            messagebox.showerror("Error", "Incorrect password.")
            return None
        else:
            if file_path in self.protected_files:
                hashed_password = self.hash_password(password)
                if hashed_password != self.protected_files[file_path]:
                    logging.error(f"Incorrect password for non-PDF {file_path}")
                    messagebox.showerror("Error", "Incorrect password.")
                    return None
            logging.debug(f"Password verified for non-PDF {file_path}")
            return password

    def test_pdf_encryption_pypdf2(self, file_path, password):
        """Test if a PDF can be opened with PyPDF2."""
        try:
            reader = PdfReader(file_path)
            if reader.is_encrypted:
                result = reader.decrypt(password)
                if result == 0:
                    logging.error(f"PyPDF2 decryption failed for {file_path}")
                    return False
                reader.pages[0]  # Access a page to ensure decryption worked
            logging.debug(f"PyPDF2 decryption succeeded for {file_path}")
            return True
        except Exception as e:
            logging.error(f"PyPDF2 test failed for {file_path}: {str(e)}")
            return False

    def test_pdf_encryption_fitz(self, file_path, password):
        """Test if a PDF can be opened with PyMuPDF."""
        try:
            doc = fitz.open(file_path)
            if doc.is_encrypted:
                if not doc.authenticate(password):
                    doc.close()
                    logging.error(f"PyMuPDF authentication failed for {file_path}")
                    return False
            doc.close()
            logging.debug(f"PyMuPDF authentication succeeded for {file_path}")
            return True
        except Exception as e:
            logging.error(f"PyMuPDF test failed for {file_path}: {str(e)}")
            return False

    def build_ui(self):
        """Build the user interface."""
        top_frame1 = Frame(self.root, bg="#1e1e1e")
        top_frame1.pack(fill=X, padx=10, pady=(10, 2))

        top_frame2 = Frame(self.root, bg="#1e1e1e")
        top_frame2.pack(fill=X, padx=10, pady=(2, 10))

        Button(top_frame1, text="Open File", command=self.select_file).pack(side=LEFT, padx=5)
        Button(top_frame1, text="Summarize 🧠", command=self.summarize_text).pack(side=LEFT, padx=5)
        self.text_entry = Entry(top_frame1, width=30)
        self.text_entry.pack(side=LEFT, padx=5)
        self.text_entry.bind("<Return>", lambda event: self.search_keyword())
        Button(top_frame1, text="Search", command=self.search_keyword).pack(side=LEFT, padx=5)
        Button(top_frame2, text="Clear Highlights", command=self.clear_highlights).pack(side=LEFT, padx=5)
        Button(top_frame2, text="Copy Text 📋", command=self.copy_text_popup).pack(side=LEFT, padx=5)
        Button(top_frame2, text="Draw Mode ✏", command=self.toggle_draw_mode).pack(side=LEFT, padx=5)
        Button(top_frame2, text="🔒 Add Password", command=self.add_password_protection).pack(side=LEFT, padx=5)
        Button(top_frame2, text="🔑 Modify Password", command=self.modify_password).pack(side=LEFT, padx=5)
        Button(top_frame2, text="❌ Remove Password", command=self.remove_password_protection).pack(side=LEFT, padx=5)
        Button(top_frame1, text="🔖 Add Bookmark", command=self.add_bookmark).pack(side=LEFT, padx=5)
        Button(top_frame1, text="❌ Remove Bookmark", command=self.remove_bookmark).pack(side=LEFT, padx=5)
        
        self.eraser_button = Button(top_frame2, text="Eraser 🧽", command=self.toggle_eraser_mode)
        self.eraser_button.pack(side=LEFT, padx=5)

        self.bookmark_listbox = Listbox(top_frame1, height=3, width=15)
        self.bookmark_listbox.pack(side=LEFT, padx=5)
        self.bookmark_listbox.bind("<<ListboxSelect>>", self.goto_bookmark)

        self.page_count_label = Label(top_frame2, text="Total Pages: 0", fg="white", bg="#1e1e1e")
        self.page_count_label.pack(side=LEFT, padx=10)

        self.current_page_label = Label(top_frame2, text="Page 0 / 0", fg="white", bg="#1e1e1e", font=("Segoe UI", 11))
        self.current_page_label.pack(side=RIGHT, padx=10)

        self.canvas = Canvas(self.root, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)

        scrollbar = Scrollbar(self.root, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.inner_frame = Frame(self.canvas, bg="#1e1e1e")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        
        self.canvas.bind("<Configure>", self.center_frame)
        self.canvas.bind("<Enter>", self._bind_scroll)
        self.canvas.bind("<Leave>", self._unbind_scroll)
        self.canvas.bind("<MouseWheel>", self.update_current_page_on_scroll)
        self.canvas.bind("<Button-4>", self.update_current_page_on_scroll)
        self.canvas.bind("<Button-5>", self.update_current_page_on_scroll)
        self.canvas.bind("<ButtonRelease-1>", self.update_current_page_on_scroll)

    def modify_password(self):
        """Modify password for the currently opened file."""
        if not self.file_path:
            messagebox.showerror("Error", "No file is currently open.")
            return

        # Check if the file is password-protected
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext == ".pdf":
            if not self.pdf_doc or not self.pdf_doc.is_encrypted:
                messagebox.showinfo("Info", "This PDF is not password-protected.")
                return
        elif self.file_path not in self.protected_files:
            messagebox.showinfo("Info", "This file is not password-protected.")
            return

        # Ask for current password
        current_password = simpledialog.askstring(
            "Current Password", 
            "Enter current password:", 
            show="*"
        )
        if not current_password:
            return

        # Verify current password
        if ext == ".pdf":
            if not self.pdf_doc.authenticate(current_password):
                messagebox.showerror("Error", "Incorrect current password.")
                return
        else:
            hashed_password = self.hash_password(current_password)
            if hashed_password != self.protected_files.get(self.file_path):
                messagebox.showerror("Error", "Incorrect current password.")
                return

        # Ask for new password
        new_password = simpledialog.askstring(
            "New Password", 
            "Enter new password:", 
            show="*"
        )
        if not new_password:
            return

        confirm_password = simpledialog.askstring(
            "Confirm Password", 
            "Confirm new password:", 
            show="*"
        )
        if new_password != confirm_password:
            messagebox.showerror("Error", "Passwords do not match.")
            return

        # Update password
        if ext == ".pdf":
            try:
                # Re-encrypt the PDF with new password
                reader = PdfReader(self.file_path)
                writer = PdfWriter()

                for page in reader.pages:
                    writer.add_page(page)

                writer.encrypt(
                    user_password=new_password,
                    owner_password=new_password,
                    permissions_flag=0b111100110100,
                    use_128bit=True
                )

                # Save to temporary file
                temp_path = self.file_path + ".temp"
                with open(temp_path, "wb") as f:
                    writer.write(f)

                # Replace original file
                os.remove(self.file_path)
                os.rename(temp_path, self.file_path)

                # Update in-memory PDF document
                self.pdf_doc.close()
                self.pdf_doc = fitz.open(self.file_path)
                self.pdf_doc.authenticate(new_password)
                self.display_pdf(self.pdf_doc)

                # Update stored password for PDFs
                self.protected_files[self.file_path] = new_password
                self.save_protected_files()

                messagebox.showinfo("Success", "PDF password updated successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to update PDF password: {str(e)}")
                logging.error(f"PDF password update failed: {str(e)}")
        else:
            # Update password for non-PDF files
            self.protected_files[self.file_path] = self.hash_password(new_password)
            self.save_protected_files()
            messagebox.showinfo("Success", "Password updated successfully!")

    # ... [Keep all other existing methods unchanged] ...

if __name__ == "__main__":
    root = Tk()
    app = SmartDocReader(root)
    root.mainloop()