int context_middle(void);
int dynamic_side_value(void);
int paired_context_middle(void);

int main(void) {
    return (paired_context_middle() == 15 &&
            context_middle() == 8 &&
            dynamic_side_value() == 27)
               ? 0
               : 1;
}
