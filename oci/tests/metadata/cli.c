extern int metadata_value(void);

int main(void) {
    return metadata_value() == 42 ? 0 : 1;
}
