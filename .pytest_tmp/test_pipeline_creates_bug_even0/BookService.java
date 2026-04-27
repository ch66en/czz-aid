package com.demo.book;

public class BookService {
    private final BookRepository bookRepository;

    public BookService(BookRepository bookRepository) {
        this.bookRepository = bookRepository;
    }

    public String getTitle(Long id) {
        Book book = bookRepository.findById(id);
        return book.getTitle();
    }

    public Book detail(Long id) {
        return bookRepository.findById(id);
    }
}
