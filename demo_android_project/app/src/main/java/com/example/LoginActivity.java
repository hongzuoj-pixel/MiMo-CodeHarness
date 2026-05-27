package com.example;

public class LoginActivity {
    public boolean validateUsername(String username) {
        return username != null && username.trim().length() >= 3;
    }

    public boolean validatePassword(String password) {
        return password != null && password.length() >= 6;
    }

    public boolean login(String username, String password) {
        return validateUsername(username) && validatePassword(password);
    }
}
